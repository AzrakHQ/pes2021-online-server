"""Reference client speaking JsonCodec; used by tests and for manual experiments."""
import asyncio, itertools
from .protocol import JsonCodec, Message


class DevClient:
    def __init__(self, host="127.0.0.1", port=10000):
        self.host, self.port = host, port
        self.codec = JsonCodec()
        self._seq = itertools.count(1)
        self.pushes: asyncio.Queue = asyncio.Queue()
        self._pending: dict[int, asyncio.Future] = {}

    async def connect(self):
        self.r, self.w = await asyncio.open_connection(self.host, self.port)
        self._task = asyncio.ensure_future(self._pump())
        return self

    async def _pump(self):
        try:
            while (m := await self.codec.read(self.r)) is not None:
                fut = self._pending.pop(m.seq, None) if m.seq else None
                (fut.set_result(m) if fut else self.pushes.put_nowait(m))
        except Exception:
            pass

    async def call(self, cmd, /, **body):
        seq = next(self._seq)
        fut = asyncio.get_running_loop().create_future()
        self._pending[seq] = fut
        self.w.write(self.codec.encode(Message(cmd, seq, body)))
        return (await asyncio.wait_for(fut, 5)).body

    async def push(self, timeout=5):
        return await asyncio.wait_for(self.pushes.get(), timeout)

    async def close(self):
        self.w.close(); self._task.cancel()
