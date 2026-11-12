"""What the gate and the web side share: the database, who is online, and the way a decoded game command
reaches its handler (pescmds.dispatch)."""
import logging
from .accounts import Accounts
from .protocol import Message

log = logging.getLogger("pesserver.service")


class Session:
    """One connection of a game to the gate. The handlers hang what they learn on it (pes_pid, mod_id, ...)."""
    def __init__(self, peer_ip, send):
        self.peer_ip = peer_ip
        self.send = send                 # coroutine(Message)


class Service:
    def __init__(self, accounts: Accounts, public_ip=None):
        self.accounts = accounts
        self.public_ip = public_ip       # override for LAN / loopback testing
        self.online: dict[int, Session] = {}

    async def handle(self, sess: Session, msg: Message) -> Message:
        if not msg.name.startswith("CMD_"):
            return Message(msg.name, msg.seq, {"ok": False, "error": "unknown command"})
        # Native PES NCL command (msgpack). Echo framing metadata so the codec replies in kind.
        from . import pescmds, pesmatch, pesrooms, pescoop, pesdivisions, pesmatchday, pesmanner, pesavatar, pesprofile, pesblock, pesreplay, pestourney, pesresults, pesranking, peswords, announce  # noqa: F401  (these register their handlers)
        body = pescmds.dispatch(self, sess, msg.name, msg.body)
        for k in ("_kind", "_seq"):
            if k in msg.body:
                body.setdefault(k, msg.body[k])
        return Message(msg.name, msg.seq, body)

    def disconnected(self, sess: Session):
        pid = getattr(sess, "pes_pid", None)
        if pid is not None and self.online.get(pid) is sess:
            self.online.pop(pid, None)
