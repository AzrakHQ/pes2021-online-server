"""The STUN endpoint the game asks for its public address."""
import asyncio, os, socket, struct, unittest
from pesserver import stun


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
