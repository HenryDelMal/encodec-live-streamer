# ECNG encoder profile

The service can encode with the jointly trained OndaBaja ECNG checkpoint as
well as the official EnCodec models. ECNG is a 24 kHz mono codec with 1.5 kbps
(2 codebooks) and 3 kbps (4 codebooks) profiles. Its ECDC-compatible segment
payloads use the `ecng_24khz` model identifier and `.ecng` filename extension;
the matching decoder weights are distributed separately with the OndaBaja
Android app.

The exact encoder model is included at
`models/ecng-24khz-encoder-f32.bin`. The native build uses the bundled
ECNG-capable `native/encodec` snapshot by default, so building ECNG does not
depend on fetching an upstream revision that may not understand the ECNG model
header. Upstream updates are opt-in with `ENCODEC_CPP_UPDATE=1`; the build
refuses a selected revision that lacks ECNG model parsing.

For a new installation, `scripts/install-systemd.sh` installs the ECNG example
configuration and prepares only its model, without downloading official
EnCodec checkpoints or setting up PyTorch. To prepare a model manually:

```sh
MODEL_DIR=/opt/encodec-live/models \
NATIVE_ENCODER=/opt/encodec-live-streamer/bin/encodec-live-native \
  /opt/encodec-live-streamer/scripts/prepare-models.sh ECNG
```

Set `codec = "ECNG"` and `samplerate = 24` in the `[stream]` table. Supported
bitrates are `bandwidth_kbps = 1.5` and `3`. Other sample rates are rejected.
Use `scripts/prepare-models.sh EnCodec` to prepare only the official models, or
omit the argument / pass `all` to prepare both codec families.

After updating an existing server, use these commands from the checkout:

```sh
git pull --ff-only
sudo editor /etc/encodec-live.toml  # set codec = "ECNG" before install
sudo ./scripts/install-systemd.sh
sudo systemctl restart encodec-live
sudo systemctl status encodec-live --no-pager
sudo journalctl -u encodec-live -n 100 --no-pager
```

The installer reads the selected codec to choose the model-preparation path,
and preserves an existing `/etc/encodec-live.toml` rather than replacing it.
If you already ran the installer before switching an existing host to ECNG,
prepare and check the ECNG model before restarting the service:

```sh
sudo env MODEL_DIR=/opt/encodec-live/models \
  NATIVE_ENCODER=/opt/encodec-live-streamer/bin/encodec-live-native \
  /opt/encodec-live-streamer/scripts/prepare-models.sh ECNG
sudo -u encodec-live /opt/encodec-live-streamer/.venv/bin/encodec-live \
  check --config /etc/encodec-live.toml
sudo systemctl restart encodec-live
```
