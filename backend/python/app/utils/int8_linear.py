"""Int8 weight storage for the linear layers of an embedding model.

Weights are kept as int8 with one fp32 scale per output row and are widened to
fp32 on every call. Activations are never quantised, so a vector does not
depend on which texts share its batch. The saving is memory (about a quarter
of the fp32 weights stay resident), not speed: the widening costs a little
time on every call.

``torch._weight_int8pack_mm`` and dynamic-activation quantisation are
deliberately not used: the first is far slower than fp32 on x86, the second
makes a text's vector depend on its co-batch.
"""

from __future__ import annotations

import torch
from torch import nn

_INT8_LIMIT = 127


def quantize_rows(weight: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """int8 weights and one fp32 scale per output row."""
    w = weight.detach().float()
    # An all-zero row would divide by zero; any positive scale reproduces it.
    scale = (w.abs().amax(dim=1) / _INT8_LIMIT).clamp_min(torch.finfo(torch.float32).tiny)
    wi = (w / scale[:, None]).round().clamp_(-_INT8_LIMIT, _INT8_LIMIT).to(torch.int8)
    return wi, scale


class Int8WeightLinear(nn.Module):
    """Drop-in for ``nn.Linear`` with int8 weights and a per-output-row scale."""

    wi: torch.Tensor
    scale: torch.Tensor
    bias: torch.Tensor | None

    def __init__(self, weight: torch.Tensor, bias: torch.Tensor | None) -> None:
        super().__init__()
        wi, scale = quantize_rows(weight)
        self._adopt(wi, scale, None if bias is None else bias.detach().float().clone())

    def _adopt(self, wi: torch.Tensor, scale: torch.Tensor, bias: torch.Tensor | None) -> None:
        self.out_features, self.in_features = wi.shape
        self.register_buffer("wi", wi)
        self.register_buffer("scale", scale)
        self.register_buffer("bias", bias)

    @classmethod
    def from_quantized(cls, wi: torch.Tensor, scale: torch.Tensor, bias: torch.Tensor | None) -> Int8WeightLinear:
        """Wrap tensors as they are (no copy), for weights read from a memory-mapped file."""
        if wi.dtype != torch.int8 or scale.dtype != torch.float32 or wi.dim() != 2 or scale.shape != (wi.shape[0],):
            raise ValueError("int8 weights need an int8 matrix and one fp32 scale per row")
        layer = cls.__new__(cls)
        nn.Module.__init__(layer)
        layer._adopt(wi, scale, bias)
        return layer

    @classmethod
    def from_linear(cls, linear: nn.Linear) -> Int8WeightLinear:
        return cls(linear.weight, linear.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        flat = x.reshape(-1, self.in_features)
        # The widened copy is at most one layer's weights (16 MB for 1024 x 4096)
        # and is released when the call returns.
        out = (flat.float() @ self.wi.float().T) * self.scale
        if self.bias is not None:
            out = out + self.bias
        return out.reshape(*x.shape[:-1], self.out_features).to(x.dtype)

    def extra_repr(self) -> str:
        return f"in_features={self.in_features}, out_features={self.out_features}, int8 weights"
