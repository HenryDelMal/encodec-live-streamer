# Android EnCodec Player compatibility

This streamer now shares the portable Eigen-based C++ EnCodec core developed in
the **Build Android EnCodec decoder** project. The Linux side uses its encoder;
the Android player uses its decoder through JNI. Model files use the same native
format, although Linux needs combined encoder/decoder weights while the APK may
ship smaller decoder-only files.

## Codec and container compatibility

The current native Android implementation accepts both stream profiles:

- `encodec_24khz`, 24 kHz mono, 75 code frames/second, no normalization scale;
- `encodec_48khz`, 48 kHz stereo, 150 code frames/second, official one-second
  frames with per-frame scale and 1% overlap;
- ECDC container version 0 with raw 10-bit code indices and `lm=false`;
- independently initialized ECDC files for every live segment.

At 3 kbps, a 24 kHz stream carries four codebooks while a 48 kHz stream carries
two. Clients must trust and validate the ECDC `m`/`nc` metadata and manifest
`init`; they must not infer the model only from bitrate.

## Live transport expectations

The Android live implementation should:

1. Poll the JSON v1 manifest with caching disabled and resolve relative segment
   URLs against the manifest URL.
2. Schedule increasing sequence numbers, maintain a small live-edge buffer, and
   validate byte length/SHA-256 before decode.
3. Select the native 24 kHz or 48 kHz decoder from `init.model`, `sample_rate`,
   and `channels`, rejecting inconsistent combinations.
4. Keep one decoder/audio sink alive while opening a fresh ECDC reader for each
   independently encoded segment.
5. Flush and rebuffer after a sequence gap, epoch change, or discontinuity.
6. Configure `AudioTrack` for 24 kHz mono or 48 kHz stereo as declared by the
   stream instead of assuming the HQ layout.

The related Android task already contains C++ decoders and ECDC parsing for both
profiles. No Python, PyTorch, ExecuTorch, Flutter, or server model file is needed
on the phone.

## Boundaries

Independently encoded chunks reset neural context. The 48 kHz model still uses
its official overlap-add *inside* each ECDC file, but neither model carries
context across numbered files. Keep direct PCM concatenation as the baseline;
if a measured seam remains, apply a short stateful client de-clicker without
changing the declared timeline. Do not normalize each segment independently,
because gain jumps can create another boundary artifact.
