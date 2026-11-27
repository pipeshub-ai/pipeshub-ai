"""MA1 QA37-U9: ``model-prices.json`` is the projection of the one price table (``MODEL_PRICES``), row for row.

Node checks a sub-agent role's cost ceiling against its release copy of that file; a price changed here without
the file (or a row added to either alone) would let a ceiling pass a model it should refuse.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness_core.kernel.budget.prices import MODEL_PRICES, PRICES_READ_ON

VECTOR: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/harness/vectors/model-prices.json").read_text(encoding="utf-8")
)


def test_qa37_u9_every_priced_model_has_its_row_and_every_row_its_model() -> None:
    assert sorted(VECTOR["usdPerMTok"]) == sorted(MODEL_PRICES)


def test_qa37_u9_each_row_is_the_short_tier_input_plus_output_price() -> None:
    table = {name: round(price.short.input + price.short.output, 6) for name, price in MODEL_PRICES.items()}
    assert VECTOR["usdPerMTok"] == table


def test_qa37_u9_the_file_names_the_day_the_prices_were_read() -> None:
    assert VECTOR["readOn"] == PRICES_READ_ON
