"""Messages bigger than one frame (body <= 0x410) arrive split across several frames."""
import asyncio, os, struct

from pesserver import ncl


def split_frames(obj, layout, key=ncl.EASY_KEY, size=1024):
    raw = ncl.msgpack.packb(obj, use_bin_type=True)
    plain = struct.pack(">IIB", len(raw), len(raw), 0) + raw      # unpadded; each frame pads its own body
    chunks = []
    if layout == "A":                       # later frames continue the plaintext
        chunks = [plain[i:i + size] for i in range(0, len(plain), size)]
    else:                                   # later frames repeat a 9-byte header
        chunks = [plain[:size]]
        rest = plain[size:]
        while rest:
            piece, rest = rest[:size - 16], rest[size - 16:]
            chunks.append(plain[:9] + piece)
    out = b""
    for c in chunks:
        c += b"\0" * (-len(c) % 8)
        out += ncl.frame(0x60, 1, ncl.bf_encrypt(c, key))
    return out


def read_all(data, codec):
    async def go():
        r = asyncio.StreamReader(); r.feed_data(data); r.feed_eof()
        msgs = []
        while (m := await codec.read(r)) is not None:
            msgs.append(m)
        return msgs
    return asyncio.run(go())


BIG = {"msgid": "CMD_SET_MYCLUB_VSCOM_GAMERESULT", "rqid": 34, "blob": os.urandom(1600).hex()[:1700]}


def test_layout_a_and_b_reassemble():
    for layout in "AB":
        data = split_frames(BIG, layout) + ncl.encode_message({"msgid": "CMD_NEXT", "rqid": 35})
        msgs = read_all(data, ncl.NclCodec())
        assert [m.name for m in msgs] == ["CMD_SET_MYCLUB_VSCOM_GAMERESULT", "CMD_NEXT"], layout
        assert msgs[0].body["blob"] == BIG["blob"]


def test_session_key_switch_still_learned():
    key = bytes(range(16))
    codec = ncl.NclCodec(); codec.offer_keys([("test", key)])
    msgs = read_all(ncl.frame(0x60, 1, ncl.bf_encrypt(ncl.pack_plain({"msgid": "CMD_X", "rqid": 1}), key)), codec)
    assert msgs[0].name == "CMD_X" and codec.key == key
