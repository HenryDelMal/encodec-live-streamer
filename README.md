# EnCodec Live Streamer

> [!CAUTION]
> **This is a vibecoded AI Slop project.** Its architecture, implementation,
> tests, deployment files, and documentation were produced largely through
> iterative AI assistance. It has not received a professional security review,
> production broadcast certification, or broad hardware compatibility testing.
> Read the source, monitor it closely, expect rough edges, and use it at your own
> risk. Do not rely on it for safety-critical or irreplaceable broadcasts.

An experimental Linux service that turns any FFmpeg-supported audio input into
a rolling live stream of independently decodable HQ Meta EnCodec files. FFmpeg
provides 48 kHz stereo PCM, Python runs Meta's official 48 kHz model, and nginx
serves an atomic JSON manifest plus immutable numbered ECDC segments.

Canonical repository:
[github.com/HenryDelMal/encodec-live-streamer](https://github.com/HenryDelMal/encodec-live-streamer)

This is an independent experiment. It is not an official Meta, Facebook,
EnCodec, FFmpeg, PyTorch, or nginx project and is not endorsed by them. The codec
architecture, reference implementation, and model weights come from
[Meta/Facebook Research's official EnCodec repository](https://github.com/facebookresearch/encodec),
which retains its own MIT license and attribution. Model weights are downloaded
at installation/runtime and are not included in this repository.

## Status and design

```text
file / ALSA / HTTP radio / Icecast / RTMP / SRT / any FFmpeg input
                                |
                    FFmpeg: stereo f32le PCM
                           48,000 Hz
                                |
                  official Meta HQ EnCodec model
                         non-LM ECDC v0
                                |
          segment-000000000000.ecdc, segment-...ecdc
                                |
                    atomic rolling stream.json
                                |
                              nginx
```

- FFmpeg owns input demuxing, reconnect options, resampling, and stereo
  conversion. FFmpeg itself is not patched.
- Every segment is a complete official ECDC v0 file with `lm=false` and can be
  decoded independently.
- Segment files and the rolling manifest are published with atomic renames.
- Sequence numbers are recovered from disk and never reused in an output
  directory.
- Service/FFmpeg restarts create a new epoch and discontinuity marker.
- Old segments are removed after a configurable manifest and grace window.
- The protocol is HLS-shaped but is **not standard HLS**. Ordinary HLS players
  do not understand EnCodec. See [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Segment duration and boundary alignment

The HQ EnCodec model uses one-second internal windows with a 47,520-sample
stride: exactly **0.99 seconds at 48 kHz**. Independent outer segments should be
an exact multiple of 0.99 seconds.

| Duration | HQ strides | Practical use |
| ---: | ---: | --- |
| `0.99` s | 1 | Lowest chunk latency; most boundary overhead |
| `1.98` s | 2 | Low-latency choice |
| `2.97` s | 3 | Middle ground |
| `3.96` s | 4 | Recommended default; tested with good results |
| `4.95` s | 5 | Fewer boundaries, higher live latency |

Avoid values such as `2.0`: they create a tiny final model frame and can make a
click at every independently encoded boundary. Alignment substantially improves
the seam but cannot guarantee mathematically gapless audio because independent
segments reset model context. A client-side boundary de-clicker can improve it
further without changing the server protocol.

The shipped default is:

```toml
segment_duration = 3.96
```

`encodec-live check` reports whether the configured duration is aligned, and the
running service logs a warning for an unaligned value.

## Requirements

- A recent mainstream Linux distribution using systemd for the supplied unit.
- Python 3.9 or newer, subject to PyTorch wheel availability for the platform.
- FFmpeg, Git, and Python virtual-environment support.
- nginx or another static HTTP server if clients need network access.
- Sufficient CPU/RAM for the HQ neural model. Real-time performance is not
  guaranteed; measure it on the intended server.
- Internet access during installation and the first checkpoint download.

The `encode` extra installs EnCodec from the current `main` branch of Meta's
official Git repository rather than the older PyPI release. A moving branch is
not reproducible, so record the resolved commit from `pip freeze` for deployments
that must be rebuilt exactly.

## Quick local run

On Debian or Ubuntu:

```bash
sudo apt update
sudo apt install -y ffmpeg git python3 python3-venv
```

Clone the canonical repository, then install into a virtual environment:

```bash
git clone https://github.com/HenryDelMal/encodec-live-streamer.git
cd encodec-live-streamer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install torch torchaudio \
  --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[encode]'
python -m pip freeze | grep -i encodec
```

For CUDA or another PyTorch platform, replace the CPU installation command with
the appropriate command from the official PyTorch installation instructions.

Create a local configuration and choose a writable output directory:

```bash
cp config/encodec-live.example.toml encodec-live.toml
mkdir -p public
```

Edit `encodec-live.toml` so `input` points to a real input and `output_dir`
points to the absolute path of `./public`. Then:

```bash
encodec-live check --config encodec-live.toml
encodec-live serve --config encodec-live.toml
```

The first encoder run downloads Meta's official checkpoint. To inspect output:

```bash
encodec-live inspect public/segment-000000000000.ecdc
python -m json.tool public/stream.json
```

For a temporary development HTTP server:

```bash
python -m http.server 8080 --directory public
```

The manifest is then `http://SERVER_IP:8080/stream.json`.

## Input examples

`input_options` are passed to FFmpeg before `-i`, where FFmpeg input options
belong. Do not add `-re` to an actual live source.

Finite file:

```toml
input = "/srv/audio/program.flac"
input_options = []
restart_ffmpeg = false
```

Loop a file at real-time speed:

```toml
input = "/srv/audio/program.flac"
input_options = ["-re", "-stream_loop", "-1"]
restart_ffmpeg = true
```

ALSA capture:

```toml
input = "hw:0"
input_format = "alsa"
input_options = ["-thread_queue_size", "1024"]
restart_ffmpeg = true
```

HTTP/Icecast radio with reconnects:

```toml
input = "https://radio.example/live.mp3"
input_options = [
  "-rw_timeout", "15000000",
  "-reconnect", "1",
  "-reconnect_streamed", "1",
  "-reconnect_delay_max", "5"
]
restart_ffmpeg = true
```

RTMP, SRT, UDP, HLS, and other inputs use the corresponding FFmpeg URL and
input options. The Python service starts a fresh FFmpeg process after EOF or
failure when `restart_ffmpeg=true`, adding a protocol discontinuity.

## Install as a systemd service

The supplied installation layout is intentionally predictable and contains no
machine-specific input or domain:

```text
/opt/encodec-live-streamer/              application source
/opt/encodec-live-streamer/.venv/        Python libraries and CLI
/opt/encodec-live-streamer/.cache/torch/ model checkpoint cache
/opt/encodec-live/public/                manifest and ECDC segments
/etc/encodec-live.toml                   administrator configuration
```

Install system packages first:

```bash
sudo apt update
sudo apt install -y ffmpeg git nginx python3 python3-venv
```

Then run the installer from a clean clone:

```bash
sudo ./scripts/install-systemd.sh
```

The installer:

- creates the unprivileged `encodec-live` account;
- copies only application source/configuration/documentation into `/opt`;
- creates the virtual environment and installs CPU PyTorch, torchaudio, and
  EnCodec from Meta's Git repository;
- creates the model cache and public output directories with deliberate
  permissions;
- installs the example configuration only when `/etc/encodec-live.toml` does
  not already exist;
- installs the systemd unit but does not start it with the placeholder input.

To use a different PyTorch wheel index:

```bash
sudo PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu128 \
  ./scripts/install-systemd.sh
```

Edit the configuration before startup:

```bash
sudo editor /etc/encodec-live.toml
sudo -u encodec-live /opt/encodec-live-streamer/.venv/bin/encodec-live \
  check --config /etc/encodec-live.toml
sudo systemctl enable --now encodec-live
sudo systemctl status encodec-live --no-pager
sudo journalctl -u encodec-live -f
```

The example unit keeps source and `.venv` root-owned/read-only. Only
`/opt/encodec-live-streamer/.cache` and `/opt/encodec-live/public` are writable
by the service. Those paths must exist before `ProtectSystem=strict` constructs
its namespace; the installer creates them. On restricted containers that do not
permit systemd mount namespaces, remove `PrivateTmp`, `ProtectSystem`,
`ProtectHome`, and `ReadWritePaths` from a local copy of the unit.

## nginx

`deploy/nginx.conf` is a generic server listening on port 8080 and serving:

```text
http://SERVER:8080/encodec/stream.json
```

Install it after reviewing the listen address, hostname, and TLS requirements:

```bash
sudo install -m 0644 deploy/nginx.conf /etc/nginx/conf.d/encodec-live.conf
sudo nginx -t
sudo systemctl reload nginx
```

The manifest is served with `Cache-Control: no-store`; numbered segments are
immutable and receive a long cache lifetime. Directory indexing is disabled.
Use TLS and authentication/access controls when exposing a stream outside a
trusted network.

## CLI

```text
encodec-live check   --config PATH   validate configuration and FFmpeg
encodec-live serve   --config PATH   run the publisher
encodec-live inspect SEGMENT.ecdc    show an ECDC header
encodec-live --version               show the service version
```

Configuration is TOML. Unknown keys, unsupported bandwidths, unsafe manifest
names, and invalid window/duration values are rejected. See the fully commented
`config/encodec-live.example.toml`.

## Tests and repository verification

Core tests do not load PyTorch or download the model:

```bash
make test
```

Before publishing a repository:

```bash
./scripts/verify-repository.sh
```

The verification script compiles Python source, runs the unit tests, checks Git
whitespace when inside a worktree, and rejects common machine-specific paths.
Generated configuration, caches, ECDC segments, `work/`, and `outputs/` are
excluded by `.gitignore`.

For a real integration smoke test, use a short non-copyrighted input and verify:

1. the manifest appears only after its referenced segment is complete;
2. `encodec-live inspect` reports `encodec_48khz`, the configured codebooks, and
   `language_model: false`;
3. each numbered file decodes independently with Meta's reference decoder;
4. encoding stays ahead of real time with stable memory use;
5. restart recovery advances sequence numbers and marks a discontinuity;
6. the segment duration is a 0.99-second multiple and boundaries sound clean.

## Android client compatibility

The emitted files match the HQ ECDC support in the experimental
[Android EnCodec Player](https://github.com/HenryDelMal/Android-encodec-player).
Live playback additionally requires manifest polling, sequence scheduling, a
persistent decoder/audio sink, buffering, integrity checks, and discontinuity
handling. See [docs/ANDROID_COMPATIBILITY.md](docs/ANDROID_COMPATIBILITY.md).

## Important limitations

- One process publishes one input and one rendition; this is not a broadcast
  control plane or transcoding farm.
- There is no built-in authentication, TLS termination, metrics server, or
  management API.
- FFmpeg's raw PCM pipe loses source PTS. Sample-relative timing is exact;
  `program_date_time` is a server-clock estimate anchored per FFmpeg epoch.
- Independently encoded chunks reset neural model context. Aligned durations
  reduce seams but do not promise gapless audio.
- Encoding is synchronous with no explicit backpressure queue. If it is slower
  than capture, FFmpeg blocks and live latency grows.
- `device="auto"` uses CUDA when PyTorch reports it; `cpu` is the predictable
  default.
- Changing codec initialization in an existing output directory starts a fresh
  manifest window while preserving monotonically increasing filenames.
- An endless official ECDC v0 file is impossible because its opening header
  declares total audio length. See [docs/PHASE_2.md](docs/PHASE_2.md) for a
  possible custom continuous transport and later FFmpeg-native integration.

## License and attribution

This service code is released under the MIT License in [LICENSE](LICENSE).
Meta's EnCodec project, checkpoint, FFmpeg, PyTorch, torchaudio, nginx, and
Android/ExecuTorch components retain their respective licenses. Review upstream
licenses before redistribution.

Issues and source updates belong in the
[GitHub repository](https://github.com/HenryDelMal/encodec-live-streamer).
