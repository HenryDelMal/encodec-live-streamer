# ELTCP v1: EnCodec Live over TCP

ELTCP v1 is an optional request/response transport over one persistent ordinary
TCP connection. It carries the existing protobuf v2 manifest and complete ECDC
files, with minimal framing. HTTP JSON/protobuf publication continues normally.
The server uses only Python's standard library and does not re-encode for TCP.

## Architecture

```text
FFmpeg -> PCM -> native EnCodec worker -> ManifestStore
                                            |
                           atomic ECDC + JSON + protobuf files
                                            |
                               immutable published snapshot
                                  /                   \
                          nginx / HTTPS        in-process TCP listener
                                  \                   /
                                client playback queue
```

Only the publisher writes the directory and rolling window. After both HTTP
manifest files have been atomically replaced, it installs an immutable snapshot
and wakes TCP manifest waiters. Each connection retains the last snapshot it
was sent; a segment index always refers to that snapshot, even when publication
continues. Network reads and writes occur in bounded client threads and never
hold the publication lock or block encoding. The snapshot's manifest bytes are
exactly the bytes in `stream.pb` for that publication.

There is one listener per publisher/stream. A connection cannot select a
different stream or supply a filesystem path. The default maximum is 32
clients; each caches at most one fetched segment for CRC retries. Snapshots
retain metadata, not entire historical segment windows. Disk cleanup retains
the configured grace window and can expire an old snapshot's files.

## Configuration and startup

All settings go in the existing `[stream]` table:

```toml
tcp_enabled = true
tcp_host = "0.0.0.0"
tcp_port = 9001
tcp_max_clients = 32
tcp_idle_timeout = 60.0
tcp_manifest_wait = 15.0
tcp_max_retries = 2
```

Defaults: disabled, host `127.0.0.1`, port 9001. Bind to an interface address,
`0.0.0.0` for all IPv4 interfaces, or an IPv6 literal such as `::1` or `::`.
Whether an IPv6 wildcard also accepts IPv4 is OS-dependent. A DNS bind name uses
IPv4. Each service instance must use a distinct address/port pair. The
provided systemd unit can listen on an unprivileged port without extra socket
activation or capabilities. There is no separate TCP process or nginx location.

`tcp_manifest_wait` must be shorter than `tcp_idle_timeout`. The idle timeout
bounds socket reads/writes, not the stream lifetime: active connections may
continue indefinitely. Configure the client's manifest-response timeout above
`tcp_manifest_wait` plus network margin. The maximum extra CRC sends is
`tcp_max_retries` (zero disables retries). Listener bind failures fail service
startup rather than silently pretending TCP is enabled. Shutdown wakes waiters
and closes sockets; service restart requires clients to reconnect and initialize.

The listener is plain TCP without encryption or authentication. Use a trusted
network or a separately configured TLS tunnel if needed. TCP failure or blocked
ports are handled by the client's paired HTTPS endpoint. HTTP publication is
not conditional on TCP being enabled.

## Integer encoding and framing

Commands and response types are single ASCII bytes, with **no newline**.
Unsigned integers use canonical unsigned LEB128, the same varint encoding as
protobuf uint64: seven low bits per byte, low groups first, high bit set when
another byte follows. Examples: 0 = `00`, 127 = `7f`, 128 = `80 01`,
300 = `ac 02`. Values are bounded to uint64 (at most 10 bytes); overflow and
noncanonical encodings are protocol errors. These are binary integers, not
decimal text. The sequence itself is uint64; a uint32 fixed-width request is
unnecessary.

Manifest and segment responses have this envelope:

```text
type: 1 byte | payload_length: unsigned varint | payload: exactly length bytes
```

Status responses contain only their one type byte. No checksum, segment ID,
title, or timestamp is duplicated in the envelope. Manifest CRC32C covers each
entire ECDC file, including its header. Decode protobuf `fixed32 crc32c` as an
unsigned 32-bit value; its four wire bytes are little-endian. Transport lengths
are unsigned varints, not fixed-width or network-byte-order integers.

TCP does not preserve message boundaries. A read may contain half a frame or
several frames. Use a buffered reader, decode one varint, then read exactly its
payload length; preserve any extra bytes for the next response. Before
allocating, enforce a client size limit (1 MiB for each manifest or segment is
ample for the provided profiles/configuration). For segment replies, require
the envelope length to equal the requested manifest entry's `byte_length`.
Use one outstanding response-producing request at a time; no request IDs are
needed and responses always correspond to the pending request.

## Client commands

| Bytes | Request | Server behavior |
| --- | --- | --- |
| `i` | Initialize/reset connection | Immediately sends `m` plus the latest protobuf manifest; establishes the index mapping. Required first command. |
| `m` | Ask for a changed manifest | Sends a new `m` frame immediately if changed, otherwise waits up to `tcp_manifest_wait`; replies `n` if still unchanged. |
| `s` + varint index | Fetch a segment | Index is zero-based in the flattened segment list of the last manifest sent on this connection. Sends `s` with raw ECDC or `g`. |
| `e` | CRC error after a complete segment response | Resends the cached last segment with an `s` frame, up to the retry limit. |
| `o` | Optional success ACK | Frees the retry cache. No response. |
| `q` | Close | Closes the connection. No response. Ordinary socket close also suffices. |

A new `s`, `i`, or `m` implicitly acknowledges the previous segment and clears
its retry cache. Avoid `o` for minimum traffic. Do not send `e` after asking for
another segment/manifest or acknowledging with `o`; it is then a protocol error.

`m` compares actual protobuf bytes against the last delivered manifest;
timestamps in JSON do not cause false changes. An `n` reply preserves the index
mapping. Every delivered `m` frame replaces it. `i` can request an immediate
refresh even if unchanged. An initial empty manifest is valid: it has no epoch
groups, and `media_sequence` is the next sequence to publish. Then request `m`
until segments are available. An unchanged finite/stalled source returns `n`
after each long-poll deadline, rather than spinning or claiming end-of-stream.

## Server responses

| Byte | Payload | Meaning / client action |
| --- | --- | --- |
| `m` | varint length + protobuf v2 | Replace the connection's manifest/index mapping. |
| `s` | varint length + complete ECDC | Validate length and manifest CRC32C, then decode. |
| `n` | None | Manifest unchanged after the wait; issue another `m` when ready. |
| `g` | None | Index unavailable, file expired/missing, or file length inconsistent; refresh with `m`/`i` and rebuffer if behind. |
| `b` | None, followed by close | Client capacity exhausted; use HTTP/backoff. May arrive before init. |
| `p` | None, followed by close | Protocol violation (unknown command, missing init, malformed varint, retry without a cached segment). |
| `r` | None, followed by close | CRC retry limit exhausted; reconnect or use HTTP. |

Socket failures/timeouts may close the connection without a status. A protocol
error status is best effort; a peer that sent unread invalid bytes may observe
a TCP reset. Receive EOF/reset at any point as connection loss. If a response
is truncated or framing/length is invalid, **reconnect**: an `e` request cannot
repair a desynchronized byte stream. Use `e` only after reading one full,
correctly framed segment whose CRC does not match.

The retry cache survives disk cleanup for that fetched segment. A repeated CRC
failure is bounded; retrying cannot fix an actually damaged file. Validate CRC
again on each retry. TCP itself already retransmits lost/corrupted packets and
delivers ordered bytes; application ACKs are optional, and no retry is needed
merely because an individual socket read returned fewer bytes than requested.

## Protobuf schema

Source of truth: [`../proto/stream.proto`](../proto/stream.proto).
Generate client bindings using that file; the schema is shared by HTTP and TCP:

```proto
syntax = "proto3";
package encodec.live.v2;

message StreamManifest {
  uint32 schema_version = 1; // Must be 2.
  uint64 media_sequence = 2;
  uint32 segment_duration_samples = 3;
  reserved 4;
  reserved "title";
  repeated EpochGroup epochs = 5;
}

message EpochGroup {
  uint64 epoch_start_unix_ms = 1;
  bool first_segment_discontinuity = 2;
  repeated Segment segments = 3;
}

message Segment {
  uint32 byte_length = 1;
  fixed32 crc32c = 2;
}
```

| Variable | Meaning |
| --- | --- |
| `schema_version` | 2; validate before using the manifest. ELTCP framing version is separately defined as v1 by this document/command set. Future incompatible framing needs a new initialization command; `i` retains v1 meaning. |
| `media_sequence` | Absolute uint64 sequence of flattened segment index 0. Sequence of index `k` is `media_sequence + k`; reject overflow. |
| `segment_duration_samples` | Fixed audio sample-frame count for every segment. Duration in seconds is this divided by the ECDC model's sample rate. |
| `epochs` | Ordered groups of consecutive segments from the same capture timeline. Flatten them in wire order for request indices. |
| `epoch_start_unix_ms` | Server-clock Unix milliseconds at the start of that capture timeline; identifies discontinuities/restarts. |
| `first_segment_discontinuity` | Applies to the first listed segment in that group. Other segments in the group are continuous. An epoch change also requires resynchronization. |
| `byte_length` | Number of bytes in the entire ECDC object; check against the TCP envelope or HTTP response. |
| `crc32c` | Unsigned Castagnoli CRC32C of the whole ECDC object. Do not use IEEE CRC32. Known vector: ASCII `123456789` -> `0xe3069283`. |

Titles are removed from both JSON and protobuf. Field 4/name `title` stay
reserved and schema v2 stays compatible with older optional-title readers.
Sample rate, model, channels, codebooks, audio length, and LM flag come from
ECDC; no `init`, per-segment `sample_count`, or second checksum is transmitted
in protobuf/TCP. Reject unsupported ECDC versions/models, LM data, or a header
audio length inconsistent with `segment_duration_samples`.

## Client scheduling and HTTP fallback

1. Store a TCP host/port **and** paired HTTPS manifest URL in client settings.
   They must refer to the same publisher. No manifest discovery fields are added.
2. Connect with a bounded connection timeout (for example 3 seconds), send `i`,
   then read one manifest frame. A blocked port or failure should promptly use
   HTTPS. On a new connection, old indices have no meaning until init completes.
3. Flatten epochs; initially select a few segments behind the live edge for
   buffering. Track the absolute next sequence to play, not just an index.
4. To fetch sequence `next`, compute `index = next - media_sequence`, verify
   it is listed, then send `s` plus the varint index. Validate length, CRC32C,
   header, and fixed duration. Decode each complete ECDC independently, while
   retaining the audio sink/queue across segments. On a full-frame CRC failure,
   send `e`; after success, request the next segment without an extra ACK.
5. Once available segments are fetched, send `m`. Keep the playback queue
   draining while waiting. On `n`, request `m` again; the server already waited,
   so no tight polling loop is needed. Client read timeout must exceed the server
   wait; 30 seconds suits the default 15-second wait plus network margin.
6. After each delivered manifest, recompute the index for the absolute next
   sequence. If that sequence is older than `media_sequence`, the client fell
   behind: jump to an appropriate live-edge position, flush/rebuffer, and honor
   epoch changes/discontinuity flags. `g` similarly triggers a refresh; repeated
   `g` with an unchanged manifest should back off or use HTTP, not spin.
7. On TCP loss, `b`, `r`, or unsupported protocol, fetch the paired HTTPS
   `stream.pb`, using the same schema, CRC, and next absolute sequence.
   For HTTP, derive the URL as `segment-%012d.ecdc`, resolved relative to the
   HTTPS manifest URL. If protobuf is unavailable, use JSON/its URI and SHA-256.
   Discard duplicate already-played sequences. If the next sequence is absent
   due to cleanup or restart, rebuffer at the live edge.
8. While HTTP continues playing, retry TCP with bounded backoff (e.g. 30 s,
   then double up to 5 min), reinitialize, and resume by absolute sequence.
   A server restart marks new segments discontinuous. Model/layout changes
   require rebuilding the decoder/audio sink. No Android source is changed here.

For unsigned protobuf fields on Android, avoid accidentally treating CRC32C as
a signed negative number: compare masked 32-bit values. Likewise preserve uint64
sequence bits or explicitly reject values outside the client's supported
positive range. Use a Castagnoli implementation supported by your minimum
Android API or provide a portable implementation.

## Example exchange and overhead

```text
C -> S: 69                         # 'i'
S -> C: 6d <varint N> <N protobuf bytes>
C -> S: 73 00                      # 's', index 0
S -> C: 73 b2 03 <434 ECDC bytes>   # length 434 = b2 03
C -> S: 73 01                      # next index, implicit success ACK
S -> C: 73 b2 03 <434 ECDC bytes>
C -> S: 65                         # optional 'e' if this whole frame fails CRC
S -> C: 73 b2 03 <same 434 bytes>
C -> S: 6d                         # 'm' after the available window is fetched
S -> C: 6e                         # 'n' after long-poll timeout if unchanged
C -> S: 6d
S -> C: 6d <varint N> <new manifest> # publication changed; indices reset
```

An index under 128 costs one varint byte: two request bytes total. A 434-byte
segment costs three response framing bytes (type + two-byte length), so fetching
it costs five application overhead bytes, excluding TCP/IP packets and optional
ACK/retry. A small unchanged-manifest cycle costs two application bytes (`m`,
`n`) after the wait. Protobuf with eight 434-byte entries in one epoch is
approximately 97-103 bytes depending on sequence/discontinuity values; no title
is included. TCP framing adds two bytes when that manifest is under 128 bytes.
Network packet headers, transport ACKs, setup, and any separate TLS tunnel still
exist; these numbers describe application bytes only.

## Verification

`make test` includes loopback integration tests for split/coalesced commands,
protobuf and ECDC framing, stable indices during rollover, expired files,
cached retries after cleanup, retry limits, long polling, malformed commands,
capacity rejection, and shutdown. Tests use synthetic ECDC headers/payloads;
they require no native encoder or model downloads. Native decoding and Android
fallback/playback integration need separate testing with the client.
