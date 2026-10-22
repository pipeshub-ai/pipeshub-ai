# pp_doclayout_s.onnx

Page gate for the pdfplumber PDF parser's layout model (see `../table_detector.py`):
a small layout detector that decides which pages are worth sending to heron.

- Source: [PaddlePaddle/PP-DocLayout-S](https://huggingface.co/PaddlePaddle/PP-DocLayout-S),
  revision `8ac289e66575bb9bba6e15c53719d8b15cc9b3b2`.
- License: Apache-2.0 (as declared by the model card).
- Converted from the Paddle inference model to ONNX with `paddle2onnx`; inputs are
  `image` (1×3×480×480, ImageNet-normalised RGB) and `scale_factor`.
- SHA-256: `e3f62a17e6d5a3e8334e979001a69bdc3c79bc45a55c0d6a98f33e657903b327`
