import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from encodec_live_streamer.config import Config
from encodec_live_streamer.ecdc import make_test_ecdc
from encodec_live_streamer.manifest import ManifestStore
from encodec_live_streamer.tcp import TcpServer, encode_varint, read_varint


def receive_exact(connection, count):
    result = bytearray()
    while len(result) < count:
        block = connection.recv(count - len(result))
        if not block:
            raise EOFError("connection closed within a payload")
        result.extend(block)
    return bytes(result)


def receive_payload(connection, expected):
    kind = receive_exact(connection, 1)
    if kind != expected:
        raise AssertionError(f"expected {expected!r}, received {kind!r}")
    return receive_exact(connection, read_varint(connection))


class TcpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        # Only the test harness uses port 0 to ask the OS for an unused port.
        # User-facing TOML validation requires a fixed port in 1..65535.
        self.config = Config(
            input="unused", output_dir=Path(self.directory.name),
            fsync=False, tcp_enabled=True, tcp_port=0, tcp_manifest_wait=0.2,
            tcp_idle_timeout=2, tcp_max_clients=2, tcp_max_retries=2,
            window_segments=2, stale_grace_segments=0,
        )
        self.store = ManifestStore(self.config)
        self.store.write_manifest()
        self.server = TcpServer(self.config, self.store)
        self.server.start()
        self.addCleanup(self.server.close)

    def connect(self):
        connection = socket.create_connection(self.server.address, timeout=2)
        self.addCleanup(connection.close)
        return connection

    def initialize(self, connection):
        connection.sendall(b"i")
        return receive_payload(connection, b"m")

    def publish(self):
        samples = self.config.samples_per_segment
        sequence = self.store.next_sequence
        payload = make_test_ecdc(samples, self.config.codebooks, self.config.model)
        self.store.publish_segment(
            payload, sample_count=samples, pts_samples=sequence * samples,
            program_date_time="2026-01-01T00:00:00Z", epoch="test",
            epoch_start_unix_ms=1767225600000, discontinuity=sequence == 0,
        )
        return payload

    def test_binary_frames_and_coalesced_optional_ack(self):
        payload = self.publish()
        connection = self.connect()
        self.assertEqual(self.initialize(connection), self.store.protobuf_path.read_bytes())
        # TCP writes can split commands or combine several commands.
        connection.sendall(b"s")
        connection.sendall(b"\x00")
        self.assertEqual(receive_payload(connection, b"s"), payload)
        connection.sendall(b"os\x00")
        self.assertEqual(receive_payload(connection, b"s"), payload)

    def test_no_change_waits_and_publication_wakes_waiters(self):
        self.publish()
        connection = self.connect()
        previous = self.initialize(connection)
        started = time.monotonic()
        connection.sendall(b"m")
        self.assertEqual(receive_exact(connection, 1), b"n")
        self.assertGreaterEqual(time.monotonic() - started, 0.15)
        connection.sendall(b"m")
        self.publish()
        current = receive_payload(connection, b"m")
        self.assertNotEqual(current, previous)
        self.assertEqual(current, self.store.protobuf_path.read_bytes())

    def test_index_stays_relative_to_last_sent_manifest_after_rollover(self):
        payload = self.publish()
        connection = self.connect()
        self.initialize(connection)
        self.publish()
        connection.sendall(b"s\x00")
        self.assertEqual(receive_payload(connection, b"s"), payload)
        self.publish()  # Sequence 0 is now deleted by cleanup.
        connection.sendall(b"s\x00")
        self.assertEqual(receive_exact(connection, 1), b"g")
        connection.sendall(b"m")
        self.assertEqual(receive_payload(connection, b"m"), self.store.snapshot().manifest)
        connection.sendall(b"s\x00")
        self.assertEqual(receive_payload(connection, b"s"), payload)

    def test_retry_uses_cached_bytes_even_after_cleanup_then_caps_retries(self):
        payload = self.publish()
        connection = self.connect()
        self.initialize(connection)
        connection.sendall(b"s\x00")
        self.assertEqual(receive_payload(connection, b"s"), payload)
        self.publish()
        self.publish()
        self.assertFalse((self.config.output_dir / "segment-000000000000.ecdc").exists())
        for _ in range(2):
            connection.sendall(b"e")
            self.assertEqual(receive_payload(connection, b"s"), payload)
        connection.sendall(b"e")
        self.assertEqual(receive_exact(connection, 1), b"r")
        self.assertEqual(connection.recv(1), b"")

    def test_invalid_commands_indexes_and_varints(self):
        connection = self.connect()
        connection.sendall(b"s")  # Invalid before init; no trailing unread bytes.
        self.assertEqual(receive_exact(connection, 1), b"p")
        self.assertEqual(connection.recv(1), b"")
        for request in (b"x", b"e", b"s\x80\x00", b"s" + b"\xff" * 10):
            connection = self.connect()
            self.initialize(connection)
            connection.sendall(request)
            self.assertEqual(receive_exact(connection, 1), b"p")
            self.assertEqual(connection.recv(1), b"")
            connection.close()
        connection = self.connect()
        self.initialize(connection)
        connection.sendall(b"s" + encode_varint((1 << 64) - 1))
        self.assertEqual(receive_exact(connection, 1), b"g")

    def test_max_clients_rejects_extra_connections(self):
        self.initialize(self.connect())
        self.initialize(self.connect())
        connection = self.connect()
        self.assertEqual(receive_exact(connection, 1), b"b")
        self.assertEqual(connection.recv(1), b"")

    def test_shutdown_wakes_manifest_requests_and_closes_sockets(self):
        connection = self.connect()
        self.initialize(connection)
        connection.sendall(b"m")
        thread = threading.Thread(target=self.server.close)
        thread.start()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        # Either close immediately or finish an already-expiring no-change reply.
        self.assertIn(connection.recv(1), (b"", b"n"))


if __name__ == "__main__":
    unittest.main()
