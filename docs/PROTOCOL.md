# EnCodec Live Protocol v1

This project uses a deliberately small, HLS-shaped protocol. It is not HLS and
must not use an `.m3u8` content type: standard HLS players do not know EnCodec.
The manifest media type is `application/vnd.encodec.live+json`.

## Transport objects

`stream.json` is an atomically replaced UTF-8 JSON document. Each segment URI
names an atomically published, complete Meta ECDC version-0 file. A client must
resolve relative segment URIs against the manifest URL. Every segment is
independently decodable and contains its own initialization header. Language
model entropy coding is always disabled.

The manifest has these fields:

| Field | Meaning |
| --- | --- |
| `format`, `version` | Fixed as `encodec-live-v1` and `1`. |
| `updated_at` | RFC 3339 UTC time at manifest publication. |
| `media_sequence` | Sequence of the first listed segment, or the next sequence when empty. |
| `discontinuity_sequence` | Number of discontinuity markers removed from the head of this rolling manifest. |
| `target_duration` | Configured nominal segment duration in seconds. The last segment of a finite input may be shorter. |
| `independent_segments` | Always `true`. |
| `init` | Stream-wide codec/container information shown below. |
| `segments` | Ordered rolling window of segment records. |

`init` fixes ECDC v0, model `encodec_48khz`, 48,000 Hz, two channels, ten bits
per codebook, configured bandwidth/codebooks, `language_model=false`, and
`self_initializing_segments=true`. The supported mappings are 3 kbps/2
codebooks, 6/4, 12/8, and 24/16.

The HQ model advances its internal one-second windows by 47,520 samples (0.99
seconds). Publishers should make independent outer segments an exact multiple
of that stride—normally 1.98, 2.97, 3.96, or 4.95 seconds. Other positive
durations remain valid protocol values, but an unaligned duration can create a
tiny final model frame and a more audible boundary seam. Version 1 recommends
3.96 seconds as the conservative default.

Each segment record contains:

| Field | Meaning |
| --- | --- |
| `sequence` | Monotonically increasing integer; never reused in an output directory. |
| `uri` | Immutable numbered ECDC object. |
| `duration`, `sample_count` | Exact presentation duration and stereo sample-frame count. |
| `pts_samples` | Zero-based presentation offset within `epoch`, in 48 kHz sample frames. |
| `program_date_time` | RFC 3339 UTC estimate anchored when this FFmpeg run starts. |
| `epoch` | UUID for one uninterrupted FFmpeg process/output timeline. |
| `discontinuity` | `true` on the first segment after service start or FFmpeg reconnect. |
| `byte_length`, `sha256` | Integrity and completeness checks for the ECDC object. |

FFmpeg raw PCM does not carry source PTS. Consequently `pts_samples` is exact
relative to captured PCM, while `program_date_time` is a server-clock estimate,
not recovered input PTS. A new epoch makes that loss of continuity explicit.

## Client algorithm

Poll the manifest without caching, initially select a segment a small number of
entries behind the live edge, then fetch segments by increasing sequence. Check
the byte length and optionally SHA-256 before decoding. A missing expected
sequence or a changed epoch/discontinuity marker requires flushing decoder/audio
timing state and rebuffering. Do not concatenate ECDC files and parse them as a
single ECDC file; open each segment independently and keep one audio output sink
alive across segment boundaries.

If the first available sequence is newer than the client's next sequence, the
client fell behind cleanup and must jump forward with a discontinuity. Poll at
roughly half `target_duration`; apply bounded retry/backoff when the manifest is
unchanged or the server is unavailable.

## Publication, cleanup, and restart

The writer creates a temporary file in the serving directory, optionally
`fsync`s it, and renames it over the final path. It publishes the segment before
atomically replacing the manifest. nginx therefore never sees a manifest that
points at a partial segment. The manifest retains `window_segments`; files remain
for an additional `stale_grace_segments` window to reduce races with clients
holding a recently replaced manifest.

At startup, the writer scans numbered segment names and resumes above the
largest sequence. It retains a compatible prior manifest window and marks the
first new segment discontinuous. A damaged/incompatible manifest is rebuilt;
sequence numbers still are not reused. Changing the codec initialization drops
the old manifest window and begins a new one. Orphan files are eventually
cleaned.

## HTTP caching

Serve the manifest with `Cache-Control: no-store, max-age=0` and no ETag. Serve
numbered segments with `Cache-Control: public, max-age=31536000, immutable` and
an ETag. Do not enable nginx directory indexes. TLS is strongly recommended for
traffic outside a trusted LAN.

## Why not one Icecast-style response?

Official ECDC v0 records total audio length in its opening header, so it cannot
represent an endless source as one valid file. A future custom continuous
transport could length-prefix complete ECDC segments on a chunked HTTP response,
but it would add reconnection/framing logic and still require a custom Android
client. Version 1 favors ordinary static HTTP objects for reliability and
inspection.
