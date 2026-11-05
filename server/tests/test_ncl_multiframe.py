"""Large messages: 0x70 first / 0x71 middle / 0x72 last frames, layout read from the game
(send path 0x1418537d0, notes/re/functions/wire.md section 1). The game rejects bodies over 0x410."""
import asyncio, os, struct

import pytest

from pesserver import ncl
from pesserver.protocol import Message, ProtocolError


def read_all(data, codec):
    async def go():
        r = asyncio.StreamReader(); r.feed_data(data); r.feed_eof()
        msgs = []
        while (m := await codec.read(r)) is not None:
            msgs.append(m)
        return msgs
    return asyncio.run(go())


def frames(data):
    """[(kind, seq, body_len)] of every frame in a byte string."""
    out, pos = [], 0
    while pos < len(data):
        kind, blen, seq = struct.unpack(">HHI", ncl.xor(data[pos:pos + 8]))
        out.append((kind, seq, blen)); pos += 24 + blen
    return out


BIG = {"msgid": "CMD_GET_NEWS_LIST", "rqid": 34, "blob": os.urandom(2500)}     # random: zlib can't shrink it


def test_small_message_stays_one_frame():
    data = ncl.encode_message({"msgid": "CMD_X", "rqid": 1, "s": "a" * 900})
    assert [k for k, _, _ in frames(data)] == [0x60]


def test_large_reply_is_split_like_the_game_does():
    data = ncl.NclCodec().encode(Message("CMD_GET_NEWS_LIST", 34, {**BIG, "_kind": 0x60, "_seq": 7}))
    fr = frames(data)
    assert [k for k, _, _ in fr] == [0x70, 0x71, 0x72]
    assert all(blen <= ncl.MAX_BODY for _, _, blen in fr) and {s for _, s, _ in fr} == {7}
    # first frame: total_len, chunk_len, original_size, zlib, chunk
    first = ncl.bf_decrypt(ncl.xor(data[24:24 + fr[0][2]]))
    total, clen, orig, comp = struct.unpack(">IIIB", first[:13])
    assert clen == ncl.FIRST_CHUNK and comp == 1 and total > ncl.SINGLE_MAX
    assert ncl.decode_stream(data)[0][2]["blob"] == BIG["blob"]


def test_push_family_uses_0x2x_and_echoed_fragment_kind_maps_back():
    assert [k for k, _, _ in frames(ncl.encode_message(BIG, kind=0x10))] == [0x20, 0x21, 0x22]
    assert [k for k, _, _ in frames(ncl.encode_message({"msgid": "CMD_X"}, kind=0x70))] == [0x60]


def test_fragments_reassemble_on_receive_then_next_message():
    for kind in (0x60, 0x10):
        data = ncl.encode_message(BIG, kind=kind) + ncl.encode_message({"msgid": "CMD_NEXT", "rqid": 35})
        msgs = read_all(data, ncl.NclCodec())
        assert [m.name for m in msgs] == ["CMD_GET_NEWS_LIST", "CMD_NEXT"]
        assert msgs[0].body["blob"] == BIG["blob"]


def test_every_size_around_the_boundaries_round_trips():
    seen = set()
    for n in range(ncl.SINGLE_MAX - 8, ncl.FIRST_CHUNK + 2 * ncl.NEXT_CHUNK + 8):
        obj = {"x": b"\1" * n}
        plains = ncl.pack_plains(obj, compress=False)
        seen.add(len(plains))
        data = ncl.frames_for(plains, 0x60, 1)
        assert all(blen <= ncl.MAX_BODY for _, _, blen in frames(data)), n
        assert ncl.decode_stream(data)[0][2] == obj, n
    assert seen == {1, 2, 3, 4}


def test_broken_sequences_are_refused():
    data = ncl.encode_message(BIG)
    fr = frames(data)
    first_len = 24 + fr[0][2]
    with pytest.raises(ProtocolError):                                  # middle frame without a first
        read_all(data[first_len:], ncl.NclCodec())
    with pytest.raises(ProtocolError):                                  # first frame then a plain message
        read_all(data[:first_len] + ncl.encode_message({"msgid": "CMD_X"}), ncl.NclCodec())


def test_session_key_switch_still_learned():
    key = bytes(range(16))
    codec = ncl.NclCodec(); codec.offer_keys([("test", key)])
    msgs = read_all(ncl.frame(0x60, 1, ncl.bf_encrypt(ncl.pack_plain({"msgid": "CMD_X", "rqid": 1}), key)), codec)
    assert msgs[0].name == "CMD_X" and codec.key == key


def test_session_key_learned_on_a_split_first_message():
    key = bytes(range(16))
    codec = ncl.NclCodec(); codec.offer_keys([("test", key)])
    msgs = read_all(ncl.frames_for(ncl.pack_plains(BIG), 0x60, 1, key), codec)
    assert msgs[0].body["blob"] == BIG["blob"] and codec.key == key
