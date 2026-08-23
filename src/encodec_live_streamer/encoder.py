from __future__ import annotations

import logging

from .ecdc import parse_header


LOG = logging.getLogger(__name__)


class EncodecEncoder:
    """Lazy wrapper around Meta's official 48 kHz EnCodec model."""

    def __init__(self, bandwidth_kbps: float, device: str = "cpu") -> None:
        try:
            import torch
            from encodec import EncodecModel
            from encodec.compress import compress
        except ImportError as error:
            raise RuntimeError(
                "EnCodec dependencies are missing; install with "
                "`python -m pip install -e '.[encode]'`"
            ) from error

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        LOG.info("loading Meta EnCodec HQ model on %s", device)
        model = EncodecModel.encodec_model_48khz().to(device)
        model.set_target_bandwidth(bandwidth_kbps)
        model.eval()
        self._torch = torch
        self._compress = compress
        self._model = model
        self._device = device
        self.bandwidth_kbps = bandwidth_kbps

    def encode(self, pcm_f32le: bytes) -> bytes:
        if len(pcm_f32le) % 8:
            raise ValueError("stereo float32 PCM length must be a multiple of 8 bytes")
        # bytearray owns writable storage and avoids PyTorch's read-only-buffer warning.
        tensor = self._torch.frombuffer(bytearray(pcm_f32le), dtype=self._torch.float32)
        waveform = tensor.reshape(-1, 2).transpose(0, 1).contiguous().to(self._device)
        payload = self._compress(self._model, waveform, use_lm=False)
        header = parse_header(payload)
        if header.model != "encodec_48khz" or header.language_model:
            raise RuntimeError("encoder produced an incompatible ECDC segment")
        return payload

