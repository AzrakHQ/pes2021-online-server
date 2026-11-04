import asyncio, os, socket, struct, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pesserver import gate, stun
from pesserver.accounts import Accounts, AccountError
from pesserver.devclient import DevClient
from pesserver.matchmaking import Matchmaker, Ticket
from pesserver.protocol import JsonCodec, Message, ProtocolError
from pesserver.service import Service


class FakeClock:
    t = 0.0
    def __call__(self): return self.t


class AccountTests(unittest.TestCase):
    def test_register_login_rating(self):
        a = Accounts()
        u1, u2 = a.register("alice", "secret1"), a.register("bob", "secret2")
        tok = a.login("alice", "secret1")
        self.assertEqual(a.user_for_token(tok)["name"], "alice")
        with self.assertRaises(AccountError): a.login("alice", "wrong")
        with self.assertRaises(AccountError): a.register("ALICE", "secret3")   # case-insensitive unique
        dw, dl = a.record_result(u1, u2)
        self.assertEqual((dw, dl), (12, -12))
        self.assertEqual(a.user_for_token(a.login("bob", "secret2"))["rating"], 988)


class MatchTests(unittest.TestCase):
    def mk(self, uid, rating, mode="ranked", nat=0): return Ticket(uid, f"u{uid}", rating, mode, ("1.2.3.4", 5000 + uid), nat)
    def test_window_widens(self):
        c = FakeClock(); m = Matchmaker(c)
        m.enqueue(self.mk(1, 1000)); m.enqueue(self.mk(2, 1400))
        self.assertEqual(m.pair(), [])               # 400 apart > 100 window
        c.t = 40                                     # window 100+10*40 = 500
        self.assertEqual(len(m.pair()), 1)
    def test_modes_isolated_and_closest_wins(self):
        m = Matchmaker()
        for t in (self.mk(1, 1000), self.mk(2, 1500, "quick"), self.mk(3, 1040), self.mk(4, 1090)):
            m.enqueue(t)
        rooms = m.pair()
        self.assertEqual({(r.host.user_id, r.guest.user_id) for r in rooms} | set(), {(1, 3)} if rooms[0].host.user_id == 1 else {(3, 1)})
        self.assertIn(2, m.queue)                    # quick-mode player alone, still waiting
    def test_double_queue_rejected(self):
        m = Matchmaker(); m.enqueue(self.mk(1, 1000))
        with self.assertRaises(ValueError): m.enqueue(self.mk(1, 1000))
    def test_report_agree_and_dispute(self):
        m = Matchmaker(); m.enqueue(self.mk(1, 1000)); m.enqueue(self.mk(2, 1000)); r = m.pair()[0]
        h, g = r.host.user_id, r.guest.user_id
        self.assertEqual(m.report(r.id, h, 2, 1)[0], "pending")
        self.assertEqual(m.report(r.id, g, 1, 2)[0], "agree")
        m.enqueue(self.mk(1, 1000)); m.enqueue(self.mk(2, 1000)); r2 = m.pair()[0]
        m.report(r2.id, 1, 3, 0); self.assertEqual(m.report(r2.id, 2, 3, 0)[0], "dispute")


class StunTests(unittest.TestCase):
    def test_binding(self):
        req = struct.pack("!HHI", 1, 0, stun.MAGIC) + os.urandom(12)
        resp = stun.build_binding_response(req, ("203.0.113.9", 40000))
        self.assertEqual(struct.unpack("!H", resp[:2])[0], 0x0101)
        self.assertEqual(resp[8:20], req[8:20])
        self.assertEqual(stun.parse_xor_mapped(resp), ("203.0.113.9", 40000))
        self.assertIsNone(stun.build_binding_response(b"junk", ("1.1.1.1", 1)))
    def test_udp_roundtrip(self):
        async def go():
            tr = await stun.serve("127.0.0.1", 0)
            port = tr.get_extra_info("sockname")[1]
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
            s.sendto(struct.pack("!HHI", 1, 0, stun.MAGIC) + os.urandom(12), ("127.0.0.1", port))
            data, _ = await asyncio.get_running_loop().run_in_executor(None, s.recvfrom, 2048)
            ip, p = stun.parse_xor_mapped(data); tr.close()
            self.assertEqual(ip, "127.0.0.1"); self.assertEqual(p, s.getsockname()[1])
        asyncio.run(go())


class EndToEnd(unittest.TestCase):
    def test_two_players_matched_and_ranked(self):
        async def go():
            acc = Accounts(); svc = Service(acc)
            srv = await gate.serve(svc, JsonCodec, "127.0.0.1", 0)
            port = srv.sockets[0].getsockname()[1]
            a, b = await DevClient(port=port).connect(), await DevClient(port=port).connect()
            self.assertFalse((await a.call("mm.queue", mode="ranked"))["ok"])        # must auth first
            for c, n in ((a, "alice"), (b, "bob")):
                self.assertTrue((await c.call("auth.register", name=n, password="pw1234"))["ok"])
            self.assertFalse((await a.call("auth.login", name="alice", password="nope"))["ok"])
            await a.call("auth.login", name="alice", password="pw1234", udp_port=7500, nat_type=0)
            await b.call("auth.login", name="bob", password="pw1234", udp_port=7501, nat_type=2)
            self.assertTrue((await a.call("mm.queue", mode="ranked"))["queued"])
            await b.call("mm.queue", mode="ranked")
            fa, fb = await a.push(), await b.push()
            self.assertEqual((fa.name, fb.name), ("match.found", "match.found"))
            self.assertEqual((fa.body["role"], fb.body["role"]), ("host", "guest"))   # lower NAT type hosts
            self.assertEqual(fa.body["opponent"]["name"], "bob"); self.assertEqual(fa.body["opponent"]["udp_port"], 7501)
            room = fa.body["room"]
            await a.call("room.ready", room=room)
            self.assertEqual((await a.call("room.report", room=room, own=2, opp=1))["verdict"], "pending")
            res = await b.call("room.report", room=room, own=1, opp=2)
            self.assertEqual(res["verdict"], "agree")
            self.assertEqual((await a.push()).name, "room.result")
            self.assertEqual((await a.call("profile.get"))["user"]["rating"], 1012)
            # disconnect during a match aborts it and tells the other player
            await a.call("mm.queue", mode="quick"); await b.call("mm.queue", mode="quick")
            await a.push(); await b.push()
            await b.close()
            self.assertEqual((await a.push()).name, "room.aborted")
            await a.close(); srv.close()
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main(verbosity=2)
