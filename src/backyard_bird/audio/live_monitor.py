"""Live audio relay for the dashboard's "listen live" button
(user request, added 2026-09-14).

Why this exists as a local TCP relay rather than the dashboard just
opening the microphone itself: it can't. ALSA's raw hw:N,M device
nodes only allow one process to have the device open at a time — not
even to just query it, confirmed live on the Raspberry Pi (see
doctor.py/README's Linux notes) — and capture_service.py already holds
the device for the entire time the system is running. So the only
process that can ever produce live audio is the capture process
itself; this module lets it relay a copy of what it's already
capturing to anyone who asks, over a small always-local TCP server.

Design constraints, in order of importance:

1. Must never be able to affect bird detection (§20.1's principle,
   extended to this feature) — if the relay port can't be bound, or
   anything else about it fails, capture must keep working exactly as
   if this feature didn't exist. See start_live_monitor_server's
   docstring.
2. A slow or stalled listener must never block capture, the analyzer,
   or any *other* listener — each client gets its own small bounded
   queue and its own thread (via socketserver.ThreadingTCPServer); a
   full queue drops the oldest buffered chunk for that one client only
   (same philosophy as capture_service.py's main _chunk_queue), never
   blocks the broadcaster.
3. A disconnected client is only actually noticed on the *next*
   broadcast() call, not the moment it disconnects: each handler
   thread blocks on its queue's get() with no timeout, so nothing
   wakes it to notice the closed socket until a chunk arrives and its
   sendall() fails. Bounded to ~0.5s in production (capture broadcasts
   continuously while running) — acceptable staleness, not a bug, but
   worth knowing if has_clients() / the client set ever gets read for
   anything more precise than "is anyone plausibly listening."
4. Bound to 127.0.0.1 only — this relay is never exposed to the LAN
   directly. The dashboard's Flask route is the sole proxy, so
   whatever access control the dashboard has (today: none beyond
   "bound to localhost by default," §23.3) is the only gate. Streaming
   raw outdoor audio is a meaningfully bigger exposure than aggregate
   detection counts if the dashboard is ever opened to a LAN with
   untrusted devices on it — worth remembering if that decision is
   revisited.
"""
from __future__ import annotations

import logging
import queue
import socketserver
import threading

logger = logging.getLogger(__name__)

_CLIENT_QUEUE_MAX_CHUNKS = 50  # ~25s of 0.5s chunks — generous but bounded, see module docstring point 2


class _MonitorRequestHandler(socketserver.BaseRequestHandler):
    server: "LiveMonitorServer"

    def handle(self) -> None:
        client_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=_CLIENT_QUEUE_MAX_CHUNKS)
        self.server.register_client(client_queue)
        try:
            while True:
                chunk_bytes = client_queue.get()
                if chunk_bytes is None:  # sentinel: server is shutting down
                    return
                self.request.sendall(chunk_bytes)
        except OSError:
            pass  # client disconnected (broken pipe, reset, etc.) — nothing more to do
        finally:
            self.server.unregister_client(client_queue)


class LiveMonitorServer(socketserver.ThreadingTCPServer):
    daemon_threads = True  # never block process exit on a lingering listener
    allow_reuse_address = True

    def __init__(self, port: int) -> None:
        super().__init__(("127.0.0.1", port), _MonitorRequestHandler)
        self._clients: set[queue.Queue] = set()
        self._clients_lock = threading.Lock()

    def register_client(self, client_queue: queue.Queue) -> None:
        with self._clients_lock:
            self._clients.add(client_queue)

    def unregister_client(self, client_queue: queue.Queue) -> None:
        with self._clients_lock:
            self._clients.discard(client_queue)

    def has_clients(self) -> bool:
        with self._clients_lock:
            return bool(self._clients)

    def broadcast(self, chunk_bytes: bytes) -> None:
        """Called from capture_service.py's main loop for every
        captured chunk. Never blocks: a full per-client queue drops
        its oldest buffered chunk rather than backing up.
        """
        with self._clients_lock:
            clients = list(self._clients)
        for client_queue in clients:
            try:
                client_queue.put_nowait(chunk_bytes)
            except queue.Full:
                try:
                    client_queue.get_nowait()
                    client_queue.put_nowait(chunk_bytes)
                except queue.Empty:
                    pass

    def shutdown_clients(self) -> None:
        """Unblocks every handle() thread's queue.get() with a
        sentinel so they exit promptly on service shutdown, rather
        than lingering until each client disconnects on its own.
        """
        with self._clients_lock:
            clients = list(self._clients)
        for client_queue in clients:
            try:
                client_queue.put_nowait(None)
            except queue.Full:
                pass


def start_live_monitor_server(port: int) -> LiveMonitorServer | None:
    """Best-effort: returns None (feature silently disabled) rather
    than raising if the port can't be bound (already in use, no
    permission, etc.) — per this module's first design constraint,
    live monitoring must never be able to prevent capture from
    running.
    """
    try:
        server = LiveMonitorServer(port)
    except OSError as exc:
        logger.warning(
            "live_monitor_start_failed", extra={"event": "live_monitor_start_failed", "error": str(exc)}
        )
        return None
    thread = threading.Thread(target=server.serve_forever, name="live-monitor-server", daemon=True)
    thread.start()
    return server
