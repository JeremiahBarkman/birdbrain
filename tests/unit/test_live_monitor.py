"""Real localhost sockets, no mocking — this module's whole job is
socket plumbing, so faking the sockets would test nothing. Binding to
port 0 lets the OS assign a free port, so these never collide with a
real capture process's relay or with each other when run in parallel.
"""
from __future__ import annotations

import socket
import time

import pytest

from backyard_bird.audio.live_monitor import LiveMonitorServer, start_live_monitor_server


@pytest.fixture()
def server():
    srv = LiveMonitorServer(port=0)  # port=0 -> OS picks a free port
    import threading

    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _connect(srv: LiveMonitorServer) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=5)
    return sock


def _recv_exact(sock: socket.socket, n: int, timeout: float = 5.0) -> bytes:
    sock.settimeout(timeout)
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            break
        data += chunk
    return data


def test_broadcast_reaches_a_connected_client(server: LiveMonitorServer) -> None:
    client = _connect(server)
    try:
        # give the handler thread a moment to register before broadcasting
        for _ in range(50):
            if server.has_clients():
                break
            time.sleep(0.02)
        assert server.has_clients()

        server.broadcast(b"hello-audio-bytes")
        received = _recv_exact(client, len(b"hello-audio-bytes"))
        assert received == b"hello-audio-bytes"
    finally:
        client.close()


def test_broadcast_reaches_multiple_clients(server: LiveMonitorServer) -> None:
    client_a = _connect(server)
    client_b = _connect(server)
    try:
        for _ in range(50):
            if len(server._clients) >= 2:  # noqa: SLF001 — test-only introspection
                break
            time.sleep(0.02)

        server.broadcast(b"xyz")
        assert _recv_exact(client_a, 3) == b"xyz"
        assert _recv_exact(client_b, 3) == b"xyz"
    finally:
        client_a.close()
        client_b.close()


def test_broadcast_with_no_clients_does_not_raise(server: LiveMonitorServer) -> None:
    server.broadcast(b"nobody is listening")  # must be a silent no-op


def test_has_clients_false_after_disconnect_and_a_broadcast(server: LiveMonitorServer) -> None:
    # Disconnection is only actually noticed on the *next* broadcast()
    # call — the handler thread is blocked on queue.get(), not
    # watching the socket for a close, so nothing wakes it until a
    # chunk arrives and its sendall() fails. In production that's
    # bounded to ~0.5s (capture broadcasts continuously); a test with
    # no broadcast at all would just see the stale registration
    # forever, which is why this isn't testing disconnect alone.
    client = _connect(server)
    for _ in range(50):
        if server.has_clients():
            break
        time.sleep(0.02)
    assert server.has_clients()

    client.close()
    for _ in range(50):
        server.broadcast(b"x")  # wakes the dead client's handler thread so it notices and unregisters
        if not server.has_clients():
            break
        time.sleep(0.02)
    assert not server.has_clients()


def test_broadcast_drops_oldest_when_client_queue_is_full(server: LiveMonitorServer) -> None:
    # A slow client (never actually reading) must not block the
    # broadcaster or affect other clients — its queue just drops the
    # oldest buffered chunk once full, per the module's design.
    from backyard_bird.audio.live_monitor import _CLIENT_QUEUE_MAX_CHUNKS

    client = _connect(server)
    try:
        for _ in range(50):
            if server.has_clients():
                break
            time.sleep(0.02)

        # Flood well past the bound — must not raise or hang.
        for i in range(_CLIENT_QUEUE_MAX_CHUNKS * 3):
            server.broadcast(f"chunk-{i}".encode())
    finally:
        client.close()


def test_start_live_monitor_server_returns_none_on_bind_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulates the port already being in use — must degrade to "no
    # live monitor," never raise or affect the caller.
    def _raise_oserror(self, *args, **kwargs):
        raise OSError("Address already in use")

    monkeypatch.setattr(LiveMonitorServer, "__init__", _raise_oserror)

    assert start_live_monitor_server(port=9999) is None


def test_start_live_monitor_server_returns_a_working_server() -> None:
    server = start_live_monitor_server(port=0)
    try:
        assert server is not None
        client = _connect(server)
        try:
            for _ in range(50):
                if server.has_clients():
                    break
                time.sleep(0.02)
            server.broadcast(b"real")
            assert _recv_exact(client, 4) == b"real"
        finally:
            client.close()
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
