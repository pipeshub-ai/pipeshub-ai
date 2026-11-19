"""Request and response models of the table-structure endpoint, imported by the service and by the client.

The request carries one cropped table image per table (a separate file part) and the words of the text layer
that lie in the box. The response carries structure only: cell spans, boxes and the ids of the request's own
words in each cell. It never carries text (``extra="forbid"`` on the response models keeps a text field out).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

SCHEMA_VERSION = 1
TOKEN_DEFINITION = "text-layer-v2"  # what a token is (see table_hybrid.TOKEN_DEFINITION); part of the hash
MODEL_REPO = "docling-project/docling-models"
MODEL_REVISION = "fc0f2d45e2218ea24bce5045f58a389aed16dc23"  # a commit, never a branch: the measured weights
MAX_TABLES_PER_REQUEST = 16
MAX_CAP = 1024  # decode steps of one table
MAX_SUM_CAPS = 1024  # decode steps of one request: one request holds a gate permit per table, never longer
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOKENS = 20_000
DEADLINE_HEADER = "X-Table-Deadline-Ms"
MAX_DEADLINE_MS = 120_000

Box = tuple[float, float, float, float]


class Token(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0)
    bbox: Box
    text: str = Field(max_length=256)


class TableSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    image: str = Field(min_length=1, max_length=64)  # name of the file part that holds the PNG crop
    scale_factor: float = Field(gt=0)
    page_w: float = Field(gt=0)
    page_h: float = Field(gt=0)
    table_bbox: Box
    cap: int = Field(ge=1, le=MAX_CAP)
    tokens: list[Token] = Field(default_factory=list)


class TableRequestMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal[1]
    tables: list[TableSpec] = Field(min_length=1, max_length=MAX_TABLES_PER_REQUEST)

    @model_validator(mode="after")
    def _limits(self) -> TableRequestMeta:
        if sum(t.cap for t in self.tables) > MAX_SUM_CAPS:
            raise ValueError("sum of caps over the request limit")
        if sum(len(t.tokens) for t in self.tables) > MAX_TOKENS:
            raise ValueError("too many tokens")
        if len({t.id for t in self.tables}) != len(self.tables):
            raise ValueError("duplicate table id")
        return self


class CellOut(BaseModel):
    model_config = ConfigDict(extra="ignore")  # an additive field never breaks an older reader

    r0: int
    r1: int
    c0: int
    c1: int
    bbox: list[float] | None = None
    tokens: list[int] = Field(default_factory=list)
    column_header: StrictBool | None = None  # the model's header tags (real booleans only); absent = null
    row_header: StrictBool | None = None


class TableResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    status: Literal["ok", "cap_hit", "error"]
    steps: int | None = None
    cpu_ms: float | None = None
    wall_ms: float | None = None
    err: str | None = None
    n_rows: int | None = None
    n_cols: int | None = None
    cells: list[CellOut] | None = None


class ModelInfo(BaseModel):
    """What produced the grids. ``config_sha256`` is the hash of the library version, the mode, the weights hash, the
    model revision and the token definition (see ``config_hash``)."""

    model_config = ConfigDict(extra="ignore")

    docling_ibm_models: str
    mode: str
    weights_sha256: str
    model_revision: str | None = None
    token_definition: str | None = None
    config_sha256: str


class TableResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    v: Literal[1] = 1
    tables: list[TableResult]
    model: ModelInfo | None = None


def config_hash(
    library_version: str, mode: str, weights_sha256: str, revision: str = MODEL_REVISION, token_definition: str = TOKEN_DEFINITION
) -> str:
    """sha256 of the library version, the mode, the weights hash, the model revision and the token definition, serialised
    with sorted keys."""
    payload = {"token_definition": token_definition, "model_revision": revision, "weights_sha256": weights_sha256, "mode": mode, "docling_ibm_models": library_version}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


ENV_MODEL_TIMEOUT = "PDF_TABLE_MODEL_TIMEOUT_S"
DEFAULT_MODEL_TIMEOUT_S = 120.0


def model_timeout_s() -> float:
    """The ceiling of one table's kill timeout, in seconds: ``PDF_TABLE_MODEL_TIMEOUT_S`` when it is a finite number above
    zero, else the default with a warning. The client and the service read it through this one function."""
    import logging

    raw = os.environ.get(ENV_MODEL_TIMEOUT, "").strip()
    if not raw:
        return DEFAULT_MODEL_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        logging.getLogger(__name__).warning("Ignoring %s=%r; using %s", ENV_MODEL_TIMEOUT, raw, DEFAULT_MODEL_TIMEOUT_S)
        return DEFAULT_MODEL_TIMEOUT_S
    return value
