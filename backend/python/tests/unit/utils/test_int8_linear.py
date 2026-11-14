"""``Int8WeightLinear``: int8 weights, per-row scale, fp32 activations."""

import pytest
import torch
from torch import nn

from app.utils.int8_linear import Int8WeightLinear, quantize_rows

BATCH_INVARIANCE_COSINE = 0.9999999


@pytest.fixture(autouse=True)
def _no_grad():  # noqa: ANN202
    with torch.no_grad():
        yield


def _cosine(a: torch.Tensor, b: torch.Tensor) -> float:
    a, b = a.double().flatten(), b.double().flatten()
    return float(a @ b / (a.norm() * b.norm()))


def _linear(n_out: int = 64, n_in: int = 96, *, seed: int = 0, spread: bool = True) -> nn.Linear:
    torch.manual_seed(seed)
    layer = nn.Linear(n_in, n_out)
    if spread:  # rows with very different magnitudes, as in trained models
        with torch.no_grad():
            layer.weight.mul_(torch.logspace(-2, 0, n_out)[:, None])
    return layer


class _DynamicActivationLinear(nn.Module):
    """Control: per-tensor activation scale taken from the whole batch."""

    def __init__(self, linear: nn.Linear) -> None:
        super().__init__()
        self.inner = Int8WeightLinear.from_linear(linear)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        scale = x.abs().max() / 127
        xq = (x / scale).round().clamp(-127, 127) * scale
        return self.inner(xq)


def _batch_dependence(layer: nn.Module, n_in: int) -> float:
    torch.manual_seed(1)
    probe = torch.randn(1, n_in)
    neighbours = torch.randn(7, n_in) * 40.0
    alone = layer(probe)
    beside = layer(torch.cat([probe, neighbours]))[:1]
    return _cosine(alone, beside)


class TestStorage:
    def test_weights_are_int8_with_a_row_scale_and_fp32_bias(self) -> None:
        src = _linear()
        layer = Int8WeightLinear.from_linear(src)
        assert layer.wi.dtype == torch.int8 and layer.wi.shape == src.weight.shape
        assert layer.scale.dtype == torch.float32 and layer.scale.shape == (src.out_features,)
        assert layer.bias.dtype == torch.float32
        assert int(layer.wi.abs().max()) <= 127

    def test_scale_is_the_row_maximum_over_127(self) -> None:
        src = _linear()
        layer = Int8WeightLinear.from_linear(src)
        assert torch.allclose(layer.scale, src.weight.abs().amax(dim=1) / 127)
        assert int(layer.wi.abs().amax(dim=1).min()) == 127  # every row uses its full range

    def test_all_zero_row_stays_zero_and_finite(self) -> None:
        src = _linear()
        with torch.no_grad():
            src.weight[3].zero_()
        layer = Int8WeightLinear.from_linear(src)
        # Without a floor the scale is 0/0 = NaN, which some CPUs cast to 0 and hide.
        assert torch.isfinite(layer.scale[3]) and layer.scale[3] > 0
        assert not layer.wi[3].any()
        out = layer(torch.randn(2, 96))
        assert torch.isfinite(out).all()
        assert torch.allclose(out[:, 3], src.bias[3].detach().expand(2), atol=1e-6)

    def test_layer_without_bias(self) -> None:
        src = nn.Linear(8, 4, bias=False)
        layer = Int8WeightLinear.from_linear(src)
        x = torch.randn(3, 8)
        assert layer.bias is None
        assert torch.allclose(layer(x), src(x), atol=5e-2)

    def test_weights_are_buffers_so_they_travel_with_the_module(self) -> None:
        layer = Int8WeightLinear.from_linear(_linear())
        assert set(layer.state_dict()) == {"wi", "scale", "bias"}
        assert list(layer.parameters()) == []


class TestMath:
    @pytest.mark.parametrize("shape", [(1, 96), (33, 96), (2, 5, 96), (3, 1, 4, 96)])
    def test_matches_fp32_within_one_percent(self, shape: tuple[int, ...]) -> None:
        src = _linear()
        layer = Int8WeightLinear.from_linear(src)
        x = torch.randn(*shape)
        ref, out = src(x), layer(x)
        assert out.shape == ref.shape
        assert float((out - ref).norm() / ref.norm()) <= 1e-2

    def test_error_comes_from_weight_rounding_only(self) -> None:
        # The same layer fed the dequantised weights in fp32 must agree to float precision.
        src = _linear()
        layer = Int8WeightLinear.from_linear(src)
        x = torch.randn(6, 96)
        dequant = layer.wi.float() * layer.scale[:, None]
        assert torch.allclose(layer(x), x @ dequant.T + layer.bias, atol=1e-4, rtol=1e-4)

    def test_non_contiguous_input(self) -> None:
        src = _linear()
        layer = Int8WeightLinear.from_linear(src)
        x = torch.randn(96, 6).T  # a transposed view
        assert not x.is_contiguous()
        assert torch.allclose(layer(x), layer(x.contiguous()))

    def test_input_dtype_is_preserved(self) -> None:
        layer = Int8WeightLinear.from_linear(_linear())
        assert layer(torch.randn(2, 96, dtype=torch.bfloat16)).dtype == torch.bfloat16


class TestBatchInvariance:
    def test_a_row_does_not_depend_on_its_neighbours(self) -> None:
        layer = Int8WeightLinear.from_linear(_linear())
        assert _batch_dependence(layer, 96) >= BATCH_INVARIANCE_COSINE

    @pytest.mark.parametrize("rows", [8, 512])
    def test_row_zero_alone_versus_in_a_larger_batch(self, rows: int) -> None:
        layer = Int8WeightLinear.from_linear(_linear())
        torch.manual_seed(2)
        x = torch.randn(rows, 96)
        assert _cosine(layer(x[:1]), layer(x)[:1]) >= BATCH_INVARIANCE_COSINE

    def test_the_test_can_see_batch_dependence(self) -> None:
        # Negative control: quantising activations per batch must fail the same check.
        control = _DynamicActivationLinear(_linear())
        assert _batch_dependence(control, 96) < BATCH_INVARIANCE_COSINE


class _Stack(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.embed = nn.Embedding(10, 16)
        self.norm = nn.LayerNorm(16)
        self.layers = nn.ModuleList([nn.Sequential(nn.Linear(16, 32), nn.Linear(32, 16)) for _ in range(2)])
        self.head = nn.Linear(16, 10)
        self.head.weight = self.embed.weight  # tied to the table


class TestFromQuantized:
    def test_wraps_the_tensors_without_copying(self) -> None:
        wi, scale = quantize_rows(torch.randn(8, 16))
        layer = Int8WeightLinear.from_quantized(wi, scale, None)
        assert layer.wi.data_ptr() == wi.data_ptr() and layer.scale.data_ptr() == scale.data_ptr()

    def test_matches_the_layer_built_from_the_fp32_weights(self) -> None:
        src = nn.Linear(16, 8)
        wi, scale = quantize_rows(src.weight)
        direct = Int8WeightLinear.from_linear(src)
        wrapped = Int8WeightLinear.from_quantized(wi, scale, src.bias.detach())
        x = torch.randn(3, 16)
        assert torch.equal(direct(x), wrapped(x))

    @pytest.mark.parametrize(
        ("wi", "scale"),
        [
            (torch.zeros(4, 4, dtype=torch.float32), torch.ones(4)),
            (torch.zeros(4, 4, dtype=torch.int8), torch.ones(3)),
            (torch.zeros(4, 4, dtype=torch.int8), torch.ones(4, dtype=torch.float64)),
            (torch.zeros(16, dtype=torch.int8), torch.ones(1)),
        ],
    )
    def test_refuses_tensors_of_the_wrong_kind(self, wi, scale) -> None:
        with pytest.raises(ValueError):
            Int8WeightLinear.from_quantized(wi, scale, None)
