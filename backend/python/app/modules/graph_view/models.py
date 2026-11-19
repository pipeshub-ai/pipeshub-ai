"""Requests, responses and the internal candidate set of the graph view.

A node id is ``<kind>:<key>`` (``block:9f2c``), so a client can
send ids back for revalidation without the server remembering what it showed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NodeId = str

MAX_ANCHORS = 20
MAX_NEW_NODES = 100
MAX_REVALIDATE_IDS = 5000
# The most rows one read takes, which bounds a cursor's offset.
MAX_READ_ROWS = 5000

# Shape only; an id that names nothing is judged by the policy, never by the schema.
_Id = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Direction = Literal["inbound", "outbound", "any"]


class ExpandRequest(BaseModel):
    source: Literal["code"]
    nodeIds: list[_Id] = Field(min_length=1, max_length=MAX_ANCHORS)
    direction: Direction = "any"
    edgeTypes: list[Annotated[str, StringConstraints(max_length=40)]] | None = Field(default=None, max_length=20)
    includeTests: bool = False
    limit: int = Field(default=50, ge=1, le=MAX_NEW_NODES)
    cursor: Annotated[str, StringConstraints(max_length=256)] | None = None


class ResolveRequest(BaseModel):
    """The address a code search hit or a record page has, which the explorer turns into a node id."""

    source: Literal["code"]
    repo: Annotated[str, StringConstraints(max_length=300)] | None = None
    recordId: _Id | None = None
    filePath: Annotated[str, StringConstraints(min_length=1, max_length=2000)] | None = None
    qualifiedName: Annotated[str, StringConstraints(min_length=1, max_length=2000)] | None = None
    includeTests: bool = False

    @model_validator(mode="after")
    def _names_a_place(self) -> "ResolveRequest":
        if not self.recordId and not (self.repo and self.filePath):
            raise ValueError("Give recordId, or repo and filePath.")
        return self


class RevalidateRequest(BaseModel):
    source: Literal["code"]
    nodeIds: list[_Id] = Field(min_length=1, max_length=MAX_REVALIDATE_IDS)
    includeTests: bool = False


class GraphNode(BaseModel):
    id: NodeId
    kind: str | None = None
    label: str | None = None
    recordId: str | None = None
    file: str | None = None
    line: int | None = None


class GraphEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    from_: NodeId = Field(alias="from")
    to: NodeId
    type: str
    confidence: str | None = None
    line: int | None = None


class Anchor(BaseModel):
    id: NodeId
    total: int


class Page(BaseModel):
    truncated: bool
    incomplete: bool
    nextCursor: str | None = None


class ExpandResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    anchors: list[Anchor]
    page: Page
    graphStatus: str


@dataclass(frozen=True)
class ExpandQuery:
    """A validated expand: ``anchors`` are ids the policy already admitted."""

    anchors: list[NodeId]
    direction: Direction
    edge_types: list[str] | None
    include_tests: bool
    limit: int
    offset: int
    rows: int = 0  # how many readable rows the source reads; the service sets it


@dataclass
class Candidates:
    """What a source found, before the policy trims it and the service pages it.

    ``edges`` are in the source's walk order, every one read (at most
    ``offset + limit`` of them when ``truncated``). ``truncated`` means the
    source stopped at its row limit with more readable rows behind it;
    ``incomplete`` means it ran out of raw scan budget first.
    """

    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    anchors: list[NodeId] = field(default_factory=list)
    truncated: bool = False
    incomplete: bool = False
    graph_status: str = "ready"
