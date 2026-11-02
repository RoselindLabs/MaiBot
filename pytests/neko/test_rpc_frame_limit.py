"""Actual shared Host/Runner framing, in-memory streams; no network/model/QQ."""

import asyncio
import struct

import pytest

from src.plugin_runtime.transport.base import Connection, MAX_FRAME_SIZE


class Writer:
    def __init__(self, peer=None):
        self.peer = peer
        self.writes = 0

    def write(self, data):
        self.writes += 1
        if self.peer:
            self.peer.feed_data(data)

    async def drain(self):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [0, 127, 23252979, 32 * 1024 * 1024])
async def test_host_runner_share_limit_and_roundtrip_both_directions(size):
    assert MAX_FRAME_SIZE == 32 * 1024 * 1024
    host_reader, runner_reader = asyncio.StreamReader(), asyncio.StreamReader()
    host = Connection(host_reader, Writer(runner_reader))
    runner = Connection(runner_reader, Writer(host_reader))
    payload = b"x" * size
    await host.send_frame(payload)
    assert await runner.recv_frame() == payload
    await runner.send_frame(payload)
    assert await host.recv_frame() == payload


@pytest.mark.asyncio
async def test_over_limit_send_writes_nothing():
    writer = Writer()
    connection = Connection(asyncio.StreamReader(), writer)
    with pytest.raises(ValueError, match="超过最大限制"):
        await connection.send_frame(b"x" * (MAX_FRAME_SIZE + 1))
    assert writer.writes == 0


@pytest.mark.asyncio
async def test_over_limit_header_rejected_before_payload_read():
    class HeaderOnlyReader:
        def __init__(self):
            self.read_sizes = []

        async def readexactly(self, size):
            self.read_sizes.append(size)
            assert size == 4
            return struct.pack(">I", MAX_FRAME_SIZE + 1)

    reader = HeaderOnlyReader()
    connection = Connection(reader, Writer())
    with pytest.raises(ValueError, match="超过最大限制"):
        await connection.recv_frame()
    assert reader.read_sizes == [4]
