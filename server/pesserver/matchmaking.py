"""Matchmaking: per-mode queues, rating-window pairing that widens with wait time, rooms."""
import itertools, time
from dataclasses import dataclass, field

MODES = ("quick", "ranked", "friendly")


@dataclass
class Ticket:
    user_id: int
    name: str
    rating: int
    mode: str
    endpoint: tuple           # (public ip, udp port) the player reported / we observed
    nat_type: int = 0
    queued_at: float = field(default_factory=time.monotonic)
    room_id: int | None = None


@dataclass
class Room:
    id: int
    mode: str
    host: Ticket
    guest: Ticket
    created: float = field(default_factory=time.time)
    reports: dict = field(default_factory=dict)     # user_id -> (own_goals, opp_goals)
    state: str = "connecting"                       # connecting -> playing -> finished/aborted


class Matchmaker:
    BASE_WINDOW = 100          # rating points
    WIDEN_PER_SEC = 10         # window growth while waiting
    MAX_WINDOW = 1500

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.queue: dict[int, Ticket] = {}
        self.rooms: dict[int, Room] = {}
        self._ids = itertools.count(1)

    def enqueue(self, t: Ticket):
        if t.mode not in MODES:
            raise ValueError("unknown mode")
        if t.user_id in self.queue:
            raise ValueError("already queued")
        if any(t.user_id in (r.host.user_id, r.guest.user_id) and r.state in ("connecting", "playing")
               for r in self.rooms.values()):
            raise ValueError("already in a match")
        t.queued_at = self.clock()
        self.queue[t.user_id] = t

    def cancel(self, user_id):
        return self.queue.pop(user_id, None) is not None

    def _window(self, t):
        return min(self.MAX_WINDOW, self.BASE_WINDOW + self.WIDEN_PER_SEC * (self.clock() - t.queued_at))

    def pair(self):
        """Pair as many compatible waiting tickets as possible. Returns the new rooms."""
        made = []
        waiting = sorted(self.queue.values(), key=lambda t: t.queued_at)
        used = set()
        for a in waiting:
            if a.user_id in used:
                continue
            best = None
            for b in waiting:
                if b.user_id == a.user_id or b.user_id in used or b.mode != a.mode:
                    continue
                diff = abs(a.rating - b.rating)
                if a.mode == "ranked" and diff > min(self._window(a), self._window(b)):
                    continue
                if best is None or diff < best[0]:
                    best = (diff, b)
            if best:
                b = best[1]
                used |= {a.user_id, b.user_id}
                # host = the player with the lower NAT type number (more open) for P2P stability
                host, guest = (a, b) if a.nat_type <= b.nat_type else (b, a)
                room = Room(next(self._ids), a.mode, host, guest)
                host.room_id = guest.room_id = room.id
                self.rooms[room.id] = room
                del self.queue[a.user_id], self.queue[b.user_id]
                made.append(room)
        return made

    def report(self, room_id, user_id, own, opp):
        """Record a result report. Returns ('pending'|'agree'|'dispute', room)."""
        room = self.rooms[room_id]
        if user_id not in (room.host.user_id, room.guest.user_id):
            raise ValueError("not in this room")
        room.reports[user_id] = (own, opp)
        if len(room.reports) < 2:
            return "pending", room
        h, g = room.reports[room.host.user_id], room.reports[room.guest.user_id]
        if h == (g[1], g[0]):
            room.state = "finished"
            return "agree", room
        room.state = "aborted"
        return "dispute", room

    def abort(self, room_id):
        r = self.rooms.get(room_id)
        if r and r.state in ("connecting", "playing"):
            r.state = "aborted"
        return r
