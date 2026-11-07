"""Protocol-independent server logic: auth, matchmaking, rooms, result reporting."""
import asyncio, logging, time
from .accounts import AccountError, Accounts
from .matchmaking import Matchmaker, Ticket
from .protocol import Message

log = logging.getLogger("pesserver.service")
VERSION_OK = None   # set to a string to enforce a client revision check


class Session:
    def __init__(self, peer_ip, send):
        self.peer_ip = peer_ip
        self.send = send                 # coroutine(Message)
        self.user = None
        self.udp_port = 0
        self.nat_type = 0


class Service:
    def __init__(self, accounts: Accounts, mm: Matchmaker | None = None, public_ip=None, allow_register=True):
        self.accounts = accounts
        self.mm = mm or Matchmaker()
        self.public_ip = public_ip       # override for LAN / loopback testing
        self.allow_register = allow_register
        self.online: dict[int, Session] = {}
        self.handlers = {
            "ping": self.h_ping, "auth.register": self.h_register, "auth.login": self.h_login,
            "auth.steam": self.h_steam, "mm.queue": self.h_queue, "mm.cancel": self.h_cancel,
            "room.ready": self.h_ready, "room.report": self.h_report, "room.abort": self.h_abort,
            "profile.get": self.h_profile,
        }

    # ---- plumbing -------------------------------------------------------------------------
    async def handle(self, sess: Session, msg: Message) -> Message:
        if msg.name.startswith("CMD_"):
            # Native PES NCL command (msgpack). Echo framing metadata so the codec replies in kind.
            from . import pescmds, pesmatch, pesrooms, pescoop, pesdivisions, pesmatchday, pesmanner, pesavatar, pesprofile, pesblock, pesreplay, pestourney, pesresults, pesranking, peswords, announce  # noqa: F401  (these register their handlers)
            body = pescmds.dispatch(self, sess, msg.name, msg.body)
            for k in ("_kind", "_seq"):
                if k in msg.body:
                    body.setdefault(k, msg.body[k])
            return Message(msg.name, msg.seq, body)
        h = self.handlers.get(msg.name)
        if not h:
            return Message(msg.name, msg.seq, {"ok": False, "error": "unknown command"})
        if msg.name.split(".")[0] in ("mm", "room", "profile") and not sess.user:
            return Message(msg.name, msg.seq, {"ok": False, "error": "not authenticated"})
        try:
            body = await h(sess, msg.body)
        except (AccountError, ValueError, KeyError) as e:
            body = {"ok": False, "error": str(e).strip("'")}
        body.setdefault("ok", True)
        return Message(msg.name, msg.seq, body)

    def disconnected(self, sess: Session):
        pid = getattr(sess, "pes_pid", None)
        if pid is not None and self.online.get(pid) is sess:
            self.online.pop(pid, None)
            self.mm.cancel(pid)
        if sess.user:
            uid = sess.user["id"]
            self.online.pop(uid, None)
            self.mm.cancel(uid)
            for r in list(self.mm.rooms.values()):
                if r.state in ("connecting", "playing") and uid in (r.host.user_id, r.guest.user_id):
                    self.mm.abort(r.id)
                    other = r.guest if r.host.user_id == uid else r.host
                    s = self.online.get(other.user_id)
                    if s:
                        asyncio.ensure_future(s.send(Message("room.aborted", 0, {"room": r.id, "reason": "opponent left"})))

    # ---- handlers -------------------------------------------------------------------------
    async def h_ping(self, s, b):
        return {"t": time.time(), "online": len(self.online), "queued": len(self.mm.queue)}

    async def h_register(self, s, b):
        if not self.allow_register:
            raise ValueError("registration closed")
        uid = self.accounts.register(b["name"], b["password"], b.get("steam_id"))
        return {"user_id": uid}

    def _bind(self, s, token, b):
        s.user = self.accounts.user_for_token(token)
        s.udp_port = int(b.get("udp_port", 0))
        s.nat_type = int(b.get("nat_type", 0))
        old = self.online.get(s.user["id"])
        if old and old is not s:                       # newest login wins
            asyncio.ensure_future(old.send(Message("session.kicked", 0, {"reason": "logged in elsewhere"})))
        self.online[s.user["id"]] = s
        return {"token": token, "user": s.user}

    async def h_login(self, s, b):
        return self._bind(s, self.accounts.login(b["name"], b["password"]), b)

    async def h_steam(self, s, b):
        return self._bind(s, self.accounts.login_steam(b["steam_id"]), b)

    async def h_profile(self, s, b):
        return {"user": self.accounts.user_by_id(s.user["id"])}

    async def h_queue(self, s, b):
        ip = self.public_ip or s.peer_ip
        port = int(b.get("udp_port", s.udp_port))
        u = self.accounts.user_by_id(s.user["id"])      # fresh rating, not the login-time snapshot
        t = Ticket(u["id"], u["name"], u["rating"], b.get("mode", "quick"), (ip, port), s.nat_type)
        self.mm.enqueue(t)
        await self._pump()
        return {"queued": t.room_id is None, "mode": t.mode}

    async def h_cancel(self, s, b):
        return {"cancelled": self.mm.cancel(s.user["id"])}

    async def _pump(self):
        for room in self.mm.pair():
            for me, other, is_host in ((room.host, room.guest, True), (room.guest, room.host, False)):
                sess = self.online.get(me.user_id)
                if sess:
                    await sess.send(Message("match.found", 0, {
                        "room": room.id, "mode": room.mode, "role": "host" if is_host else "guest",
                        "opponent": {"name": other.name, "rating": other.rating,
                                     "ip": other.endpoint[0], "udp_port": other.endpoint[1],
                                     "nat_type": other.nat_type}}))

    async def h_ready(self, s, b):
        r = self.mm.rooms[int(b["room"])]
        if s.user["id"] not in (r.host.user_id, r.guest.user_id):
            raise ValueError("not in this room")
        r.state = "playing"
        return {"state": r.state}

    async def h_report(self, s, b):
        room_id = int(b["room"])
        verdict, room = self.mm.report(room_id, s.user["id"], int(b["own"]), int(b["opp"]))
        out = {"verdict": verdict}
        if verdict == "agree" and room.mode == "ranked":
            h, g = room.reports[room.host.user_id]
            if h != g:
                w, l = (room.host, room.guest) if h > g else (room.guest, room.host)
                out["delta"] = self.accounts.record_result(w.user_id, l.user_id)
            else:
                out["delta"] = self.accounts.record_result(room.host.user_id, room.guest.user_id, draw=True)
        if verdict != "pending":
            for me in (room.host, room.guest):
                sess = self.online.get(me.user_id)
                if sess and me.user_id != s.user["id"]:
                    await sess.send(Message("room.result", 0, {"room": room_id, **out}))
        return out

    async def h_abort(self, s, b):
        r = self.mm.abort(int(b["room"]))
        return {"state": r.state if r else "gone"}
