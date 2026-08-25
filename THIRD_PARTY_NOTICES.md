# Third-party notices

## Meta EnCodec

The generated 24 kHz and 48 kHz combined model files contain parameters from
[Meta's official EnCodec project](https://github.com/facebookresearch/encodec),
distributed under its MIT license. Model weights are not committed here.

## encodec.cpp

The portable native encoder/decoder is derived from
[pfeatherstone/encodec.cpp](https://github.com/pfeatherstone/encodec.cpp) and
the dual-model work used by
[HenryDelMal/encodec.cpp](https://github.com/HenryDelMal/encodec.cpp). It is
distributed under the MIT License; the complete notice is included at
`native/encodec/LICENSE`.

## Eigen

The native runtime vendors Eigen headers. Eigen is primarily distributed under
the Mozilla Public License 2.0. The included license is at
`native/third_party/eigen/COPYING.MPL2`.
