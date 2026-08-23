# Android EnCodec Player compatibility assessment

Assessment target: <https://github.com/HenryDelMal/Android-encodec-player>,
repository state inspected on 2026-08-22. No changes were made to that project.

## What already matches

The player's ECDC reader accepts the exact segment body produced here:

- ECDC container version 0;
- `encodec_48khz`, 48 kHz stereo;
- 2, 4, 8, or 16 codebooks (nominal 3, 6, 12, or 24 kbps);
- raw 10-bit code indices with `lm=false`;
- the official one-second HQ frame layout, per-frame scale, and 1% overlap.

Each server segment can therefore be passed to a fresh `EcdcReader` and decoded
by the existing `ExecuTorchEncodecDecoder`; no decoder model or ECDC bit unpacker
change is required.

## Android work required

The current app accepts local files and direct HTTPS URLs for finite `.ecdc`
objects. It treats one URL as one playlist track and closes/recreates playback
around tracks. It does not parse `stream.json` or follow numbered live segments.

A future Android change should add:

1. A live-source type and JSON v1 manifest model/parser with strict validation.
2. A poller using cache-disabled requests, relative-URI resolution, bounded
   retry/backoff, and a selectable live-edge buffer (two or three segments is a
   reasonable start).
3. A segment scheduler keyed by sequence, with byte-length/SHA-256 validation,
   late-window recovery, and no duplicate playback after manifest refreshes.
4. Reuse of one `ExecuTorchEncodecDecoder` and one `AudioTrackSink` while opening
   a fresh `EcdcReader` per segment. The current finite-file playback session
   should be refactored rather than nesting one session per segment.
5. Discontinuity handling that drains or flushes queued PCM, resets timing, and
   re-buffers on `discontinuity=true`, an epoch change, or a sequence gap.
6. Live UI semantics: `LIVE` state, latency/buffer display, no finite seek bar,
   and a jump-to-live action. Seeking can later be bounded to listed/retained
   segments.
7. Lifecycle cancellation and network tests for unchanged manifests, atomic
   window rollover, 404 from cleanup, restart epochs, corrupt hashes, and slow
   decode/network conditions.

HQ segment boundaries may produce a small audible seam because independently
encoded chunks reset model context. The existing decoder already handles the
official overlap *inside* each ECDC file; it does not crossfade between separate
files. Start with direct PCM concatenation and measure. If needed, add a short
client crossfade, while ensuring it does not shorten the declared timeline.

## Compatibility conclusion

Codec compatibility is high; transport compatibility is not yet implemented.
The required work is concentrated in Android networking/scheduling/playback
lifecycle code, not EnCodec inference. The current app cannot play the live
manifest until that work lands, but it can be used to download and manually
play any individual emitted segment during server testing.

