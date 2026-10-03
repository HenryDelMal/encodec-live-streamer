"""ELTCP v1: persistent, framed requests over ordinary TCP (docs/TCP_PROTOCOL.md)."""
from __future__ import annotations

import logging
import socket
import socketserver
import threading

from .config import Config
from .manifest import ManifestStore, PublicationSnapshot


LOG = logging.getLogger(__name__)


class ProtocolError(ValueError):
    pass


def encode_varint(value: int) -> bytes:
    """Canonical unsigned LEB128, bounded to uint64."""
    if not 0 <= value < 1 << 64:
        raise ValueError("varint must fit uint64")
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def read_varint(connection: socket.socket) -> int:
    value = 0
    for index in range(10):
        raw = connection.recv(1)
        if not raw:
            raise ProtocolError("truncated varint")
        byte = raw[0]
        if index == 9 and byte > 1:
            raise ProtocolError("varint exceeds uint64")
        value |= (byte & 127) << (7 * index)
        if byte < 128:
            if index and byte == 0:
                raise ProtocolError("noncanonical varint")
            return value
    raise ProtocolError("unterminated varint")


def send_payload(connection: socket.socket, kind: bytes, payload: bytes) -> None:
    # One write avoids Nagle/delayed-ACK stalls between header and payload.
    connection.sendall(kind + encode_varint(len(payload)) + payload)


class _RequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = self.server
        assert isinstance(server, _Server)
        connection = self.request
        connection.settimeout(server.config.tcp_idle_timeout)
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        window: PublicationSnapshot | None = None
        last_segment: bytes | None = None
        retries = 0
        try:
            while not server.stopping.is_set():
                command = connection.recv(1)
                if not command or command == b"q":
                    return
                if window is None and command != b"i":
                    raise ProtocolError("connection must begin with i")
                if command in (b"i", b"m"):
                    last_segment = None
                    retries = 0
                    if command == b"i":
                        current = server.store.snapshot()
                    else:
                        assert window is not None
                        current = server.store.wait_snapshot(
                            window.manifest,
                            server.config.tcp_manifest_wait,
                            server.stopping,
                        )
                    if server.stopping.is_set():
                        return
                    if command == b"m" and current.manifest == window.manifest:
                        connection.sendall(b"n")
                    else:
                        send_payload(connection, b"m", current.manifest)
                        window = current
                elif command == b"s":
                    assert window is not None
                    index = read_varint(connection)
                    last_segment = None
                    retries = 0
                    if index >= len(window.segments):
                        connection.sendall(b"g")
                        continue
                    item = window.segments[index]
                    try:
                        payload = (server.store.output_dir / item.uri).read_bytes()
                    except FileNotFoundError:
                        connection.sendall(b"g")
                        continue
                    if len(payload) != item.byte_length:
                        connection.sendall(b"g")
                        continue
                    last_segment = payload
                    send_payload(connection, b"s", payload)
                elif command == b"e":
                    if last_segment is None:
                        raise ProtocolError("no segment available to retry")
                    if retries >= server.config.tcp_max_retries:
                        connection.sendall(b"r")
                        return
                    retries += 1
                    send_payload(connection, b"s", last_segment)
                elif command == b"o":
                    # Optional success ACK. Next s/i/m also acknowledges it.
                    last_segment = None
                    retries = 0
                else:
                    raise ProtocolError("unknown command")
        except ProtocolError as error:
            LOG.debug("TCP protocol error from %s: %s", self.client_address, error)
            try:
                connection.sendall(b"p")
            except OSError:
                pass
        except OSError as error:
            LOG.debug("TCP connection ended from %s: %s", self.client_address, error)


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(self, address: tuple[str, int], config: Config, store: ManifestStore):
        self.config = config
        self.store = store
        self.stopping = threading.Event()
        self._slots = threading.BoundedSemaphore(config.tcp_max_clients)
        self._connections: set[socket.socket] = set()
        self._connections_lock = threading.Lock()
        self.address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        self.request_queue_size = min(config.tcp_max_clients, 128)
        super().__init__(address, _RequestHandler)

    def process_request(self, request: socket.socket, client_address: tuple) -> None:
        if not self._slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(b"b")
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        with self._connections_lock:
            self._connections.add(request)
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self._connections_lock:
                self._connections.discard(request)
            self._slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: tuple) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._connections_lock:
                self._connections.discard(request)
            self._slots.release()

    def stop_connections(self) -> None:
        with self._connections_lock:
            connections = tuple(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class TcpServer:
    """Bounded thread-per-client server; encoding/publication never waits on clients."""

    def __init__(self, config: Config, store: ManifestStore) -> None:
        self._server = _Server((config.tcp_host, config.tcp_port), config, store)
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple:
        return self._server.server_address

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("TCP server already started")
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="encodec-live-tcp",
            daemon=True,
        )
        self._thread.start()
        LOG.info("ELTCP v1 listening on %s", self.address)

    def close(self) -> None:
        self._server.stopping.set()
        self._server.store.wake_snapshot_waiters()
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join()
        self._server.stop_connections()
        self._server.server_close()
