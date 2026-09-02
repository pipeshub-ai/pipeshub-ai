"""Which images get sent as pixels, and which degrade to text.

Every image block reaches the model as text no matter what (see
`chat_helpers.image_block_text`). This module decides the separate question
of which of them *also* get their pixels attached, under two ceilings:

* `ImagePolicy.max_images_per_request` -- what the model in use accepts, and
  below that, how many still help rather than hurt (`image_policy.py`).
* `ImageBudget` -- the conversation-wide ceiling that already existed, kept
  as a coarse outer bound across every source in a request.

The two compose rather than replace: the budget counts, the policy decides,
this class selects.

Selection is deliberately not first-come-first-served, which is what the bare
counter gave us. A 40-page PDF whose every page carries the same header logo
would spend the whole allowance on the logo before reaching the chart the
query was about, and multi-image benchmarks are clear that irrelevant images
actively degrade answers rather than merely wasting tokens. So candidates are
filtered (icons, rules, duplicates), ranked (who produced it, then how
relevant, then how large), and truncated to the cap.

Two properties the callers depend on:

* **Nothing vanishes.** `admit()` returns every candidate, partitioned --
  each one either admitted or degraded with a reason. A caller that renders
  both lists cannot silently drop content.
* **One picture, one copy.** An image already admitted in this request is a
  duplicate on every later `admit()`, not a second admission. It still costs
  no second slot and no second charge against the budget, and the copy that
  did go out stays exactly where it was -- so re-fetching a record
  mid-conversation neither double-charges nor reshuffles what the model
  already saw, which would invalidate the provider's prompt cache. What it no
  longer does is hand the caller another copy to materialize: every caller
  attaches what it is given, so an image that arrived as a search hit and
  again in a fetch of the record it lives in used to reach the wire twice
  while the cap counted it once. The transport guard then had to cut the
  overflow, evicting pictures the model had not seen to keep repeats of one
  it had (`agents/agent_loop/image_guard.py`).
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import TYPE_CHECKING

from app.utils.image_utils import (
    MAX_CONTENT_ASPECT_RATIO,
    MIN_CONTENT_AREA_PX,
    MIN_CONTENT_SHORT_EDGE_PX,
    is_below_content_size,
    is_extreme_aspect_ratio,
)

if TYPE_CHECKING:
    from app.utils.image_policy import ImagePolicy

# Outer ceiling on images across a whole conversation, independent of which
# model is answering. Lives here with the rest of the image-admission rules;
# `chat_helpers` re-exports it, which is where the codebase has always
# imported it from.
MAX_IMAGES_IN_CONVERSATION = 50


class ImageBudget:
    """Conversation-wide image counter shared across every image source (user
    attachments, history replay, search/fetch/prefetch tool results).

    A single instance is threaded through a turn so the same ceiling applies
    no matter which source contributed the image. It counts; it does not
    decide -- `ImageAdmission` owns the per-model cap and the selection.
    """

    def __init__(self, max_images: int = MAX_IMAGES_IN_CONVERSATION) -> None:
        self.max_images = max_images
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_images - self.used)

    def can_add(self) -> bool:
        return self.used < self.max_images

    def try_consume(self, count: int = 1) -> int:
        """Consume up to `count` from the budget. Returns the amount actually
        consumed (may be less than `count` near the ceiling)."""
        actual = min(count, self.remaining)
        self.used += actual
        return actual

# The "is this page furniture" thresholds live in `image_utils` because
# indexing asks the same question before paying a VLM to describe an image;
# re-deciding it here would let the two drift.
MIN_SHORT_EDGE_PX = MIN_CONTENT_SHORT_EDGE_PX
MIN_AREA_PX = MIN_CONTENT_AREA_PX
MAX_ASPECT_RATIO = MAX_CONTENT_ASPECT_RATIO
# Rank for an image whose dimensions could not be read: neutral, so it sorts
# below a real figure but above a thumbnail rather than last.
_UNKNOWN_AREA_PX = 400 * 400


class ImageOrigin(IntEnum):
    """Who produced this image. Ordered by claim on a scarce slot -- a user's
    own attachment must never lose to a search thumbnail that happens to be
    higher-resolution, so origin is a hard tier and not one weight among
    several."""

    ATTACHMENT = 0       # the user uploaded it on this or an earlier turn
    FETCHED_RECORD = 1   # the model explicitly fetched the record it lives in
    SEARCH_HIT = 2       # retrieval surfaced it
    HISTORY = 3          # replayed from an earlier turn


class DegradeReason(str, Enum):
    """Why an image is going out as text only. Carried back to the renderer
    so the marker can say something true, and to logs so an operator can tell
    'the model can't take images' apart from 'it could, but this one lost'."""

    TEXT_ONLY_MODEL = "text_only_model"
    TOO_SMALL = "too_small"
    DECORATIVE = "decorative"
    DUPLICATE = "duplicate"
    OVER_REQUEST_CAP = "over_request_cap"
    OVER_CONVERSATION_CAP = "over_conversation_cap"


def content_hash(data_uri: str) -> str:
    """Stable identity for an image's bytes, so the same figure arriving from
    two sources (prefetch and a later fetch, or the same logo on 40 pages) is
    recognized as one image."""
    return hashlib.sha256(data_uri.encode("utf-8", errors="ignore")).hexdigest()[:16]


@dataclass(frozen=True)
class ImageCandidate:
    """One image, plus everything a selection decision needs.

    `text` is the fallback prose (`chat_helpers.image_block_text`) and is
    always rendered -- admitted or not -- so this type carries it rather than
    leaving the renderer to look it up twice.
    """

    ref: str
    data_uri: str
    origin: ImageOrigin
    text: str = ""
    relevance: float = 0.0
    block_index: int = 0
    turn_index: int = 0
    width: int | None = None
    height: int | None = None
    virtual_record_id: str | None = None

    @property
    def area(self) -> int:
        if self.width and self.height:
            return self.width * self.height
        return _UNKNOWN_AREA_PX

    @property
    def hash(self) -> str:
        return content_hash(self.data_uri)


@dataclass(frozen=True)
class DegradedImage:
    candidate: ImageCandidate
    reason: DegradeReason


@dataclass(frozen=True)
class AdmissionResult:
    """Every candidate, partitioned. `admitted + degraded` is always the input
    set -- see the module docstring's "nothing vanishes"."""

    admitted: list[ImageCandidate] = field(default_factory=list)
    degraded: list[DegradedImage] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.admitted) + len(self.degraded)


def _prefilter_reason(candidate: ImageCandidate) -> DegradeReason | None:
    """Why this image is not worth a slot on its own merits, or None.

    Only consulted when images actually compete for slots (see `admit`), and
    never for a user's own attachment: a small image someone deliberately
    uploaded is the subject of their question, not page furniture.

    Dimensions are often unknown (only the pdfplumber parser records them and
    a data URI may not have been measured), and an unmeasured image is never
    rejected -- the cost of dropping a real figure is far higher than the cost
    of spending a slot on a logo.
    """
    if candidate.origin is ImageOrigin.ATTACHMENT:
        return None
    if is_below_content_size(candidate.width, candidate.height):
        return DegradeReason.TOO_SMALL
    if is_extreme_aspect_ratio(candidate.width, candidate.height):
        return DegradeReason.DECORATIVE
    return None


def _rank_key(candidate: ImageCandidate) -> tuple[int, float, int, int, int]:
    """Sort key for a scarce slot: origin tier, then relevance, then size,
    then recency, with document order as a deterministic final tiebreak.

    Size enters below relevance rather than above it: a large image is a
    weak signal that it is a figure rather than a thumbnail, but a small
    diagram the query actually asked about still outranks a big decorative
    banner that merely scored well on nothing.
    """
    return (
        int(candidate.origin),
        -candidate.relevance,
        -candidate.area,
        -candidate.turn_index,
        candidate.block_index,
    )


class ImageAdmission:
    """Per-request arbiter of which images are sent as pixels.

    Lives on `context.tool_state["image_admission"]`, seeded once per request
    beside the `image_budget` it composes, and consulted by every renderer.
    """

    def __init__(self, policy: "ImagePolicy", budget: ImageBudget | None = None) -> None:
        self.policy = policy
        # The conversation-wide ceiling. Composed, not replaced: it answers
        # "how many across the whole conversation", this class answers "which
        # ones, for this model".
        self.budget = budget if budget is not None else ImageBudget()
        # Hashes already sent this request. Gives idempotent re-renders and a
        # stable image set across turns (see the module docstring).
        self._admitted_hashes: set[str] = set()
        # Original hash -> the bytes actually admitted for it, whenever
        # `_normalize` had to rewrite them. Kept because the renderers hold a
        # decision, not a candidate, and re-read the source block's own URI:
        # without somewhere to look the downscale would be computed and thrown
        # away, and an image over the provider's per-image byte limit would go
        # to the wire at full size. Only holds entries that actually changed.
        self._normalized_by_hash: dict[str, str] = {}
        # Hashes whose normalization has been decided, including the ones that
        # needed no rewrite. Without it a no-op re-decodes on every render.
        self._normalization_checked: set[str] = set()

    @property
    def allows_images(self) -> bool:
        return self.policy.allows_images

    @property
    def remaining(self) -> int:
        """Slots left for *new* images -- the tighter of the two ceilings."""
        if not self.policy.allows_images:
            return 0
        per_request = self.policy.max_images_per_request - len(self._admitted_hashes)
        return max(0, min(per_request, self.budget.remaining))

    def already_admitted(self, candidate: ImageCandidate) -> bool:
        return candidate.hash in self._admitted_hashes

    def admit(self, candidates: list[ImageCandidate]) -> AdmissionResult:
        """Partition `candidates` into pixels-and-text versus text-only.

        Safe to call repeatedly within a request; each call only spends slots
        on images not already admitted.
        """
        if not candidates:
            return AdmissionResult()

        if not self.policy.allows_images:
            return AdmissionResult(
                degraded=[
                    DegradedImage(c, DegradeReason.TEXT_ONLY_MODEL) for c in candidates
                ],
            )

        admitted: list[ImageCandidate] = []
        degraded: list[DegradedImage] = []
        fresh: list[ImageCandidate] = []
        seen_in_batch: set[str] = set()

        for candidate in candidates:
            digest = candidate.hash
            # The same bytes twice teach the model nothing, and that holds
            # whether the copy that won the slot went out in this batch or an
            # earlier one this request. Deduplication is right whether or not
            # slots are scarce; the pixels are already in the request, and the
            # caller renders this one's text either way.
            if digest in self._admitted_hashes or digest in seen_in_batch:
                degraded.append(DegradedImage(candidate, DegradeReason.DUPLICATE))
                continue
            seen_in_batch.add(digest)
            fresh.append(candidate)

        # The prefilter exists to stop page furniture from crowding out real
        # figures, so it only runs when there is crowding. A record whose one
        # image is a 40x40 diagram still gets sent when a slot is free --
        # dropping it on a size rule would mean an explicit fetch returned no
        # picture at all.
        # With no slots left at all there is nothing to choose between, and a
        # capacity reason tells the reader more than "too small" would.
        if self.remaining > 0 and len(fresh) > self.remaining:
            eligible: list[ImageCandidate] = []
            for candidate in fresh:
                reason = _prefilter_reason(candidate)
                if reason is not None:
                    degraded.append(DegradedImage(candidate, reason))
                else:
                    eligible.append(candidate)
        else:
            eligible = fresh

        for candidate in sorted(eligible, key=_rank_key):
            if self.policy.max_images_per_request - len(self._admitted_hashes) <= 0:
                degraded.append(DegradedImage(candidate, DegradeReason.OVER_REQUEST_CAP))
                continue
            if not self.budget.can_add():
                degraded.append(DegradedImage(candidate, DegradeReason.OVER_CONVERSATION_CAP))
                continue
            _ = self.budget.try_consume(1)
            self._admitted_hashes.add(candidate.hash)
            admitted.append(self._normalize(candidate))

        # Ranking decided membership; document order decides presentation, so
        # what the model reads still matches the record it came from.
        #
        admitted.sort(key=lambda c: (c.turn_index, c.block_index))
        return AdmissionResult(admitted=admitted, degraded=degraded)

    def _normalize(self, candidate: ImageCandidate) -> ImageCandidate:
        """Fit an admitted image to the model's per-image limits.

        Runs only on images that won a slot, and only rewrites the ones that
        actually exceed a limit -- see `downscale_to_limits`. Done here rather
        than at each renderer so every delivery path (tool result, user
        message, history replay) gets it from one place.
        """
        self._decide_normalization(candidate.data_uri)
        normalized = self._normalized_by_hash.get(candidate.hash)
        if normalized is None:
            return candidate
        # Dimensions changed with the bytes; drop the stale ones rather than
        # letting a later token estimate quote the original raster.
        return replace(candidate, data_uri=normalized, width=None, height=None)

    def _decide_normalization(self, data_uri: str) -> None:
        """Work out the admitted bytes for `data_uri`, once.

        Pure apart from the two caches it fills, and safe to run on a worker
        thread -- which is the point: `downscale_to_limits` is a Pillow decode,
        resize and re-encode, ~600 ms for a 4000x3000 image, and the render
        that calls it sits on an async request path.
        """
        from app.utils.image_utils import downscale_to_limits

        digest = content_hash(data_uri)
        if digest in self._normalization_checked:
            return
        normalized = downscale_to_limits(
            data_uri,
            max_long_edge_px=self.policy.max_long_edge_px,
            max_bytes=self.policy.max_bytes_per_image,
        )
        self._normalization_checked.add(digest)
        if normalized is not data_uri and normalized != data_uri:
            # Keyed by the ORIGINAL hash: that is what a renderer holds and
            # what a later batch will present again.
            self._normalized_by_hash[digest] = normalized

    async def warm(self, data_uris: "list[str]") -> None:
        """Precompute the downscales `data_uris` will need, off the event loop.

        The render itself is synchronous and called from `async` tool paths;
        letting it hit Pillow inline blocks the loop for every concurrent
        request, not just this one. Doing the decode here leaves the render
        with cache hits. Only the image work moves to the worker thread --
        admission's own bookkeeping stays on the loop, since nothing here is
        thread-safe against a second caller.
        """
        if not self.policy.allows_images:
            return
        todo = [
            uri for uri in dict.fromkeys(data_uris)
            if uri and content_hash(uri) not in self._normalization_checked
        ]
        if not todo:
            return
        await asyncio.to_thread(self._decide_all, todo)

    def _decide_all(self, data_uris: list[str]) -> None:
        for uri in data_uris:
            self._decide_normalization(uri)

    def admitted_uri(self, data_uri: str) -> str | None:
        """The bytes to send for `data_uri`, or None if it was not admitted.

        `admit()` returns *normalized* candidates, whose hash is the hash of
        the downscaled bytes -- so a caller holding the source URI cannot look
        its verdict up in that list. `_admitted_hashes` is keyed by the
        original, which is what a caller actually has. Answers both questions
        from one hash: these are sha256 over the whole base64 payload, and at
        tens of megabytes per image that cost is worth counting.

        Answers "did this image win a slot in this request", not "did MY
        batch win it": a duplicate of something admitted earlier still has an
        admitted hash. A caller that materializes what it looks up therefore
        has to skip what its own `admit()` returned as degraded first, or it
        re-emits the copy that call just rejected.
        """
        digest = content_hash(data_uri)
        if digest not in self._admitted_hashes:
            return None
        return self._normalized_by_hash.get(digest, data_uri)

    def rendered_uri(self, data_uri: str) -> str:
        """The bytes to actually send for `data_uri`.

        The downscaled form when this image needed one to fit the model's
        per-image limits, else `data_uri` unchanged. Renderers call this
        instead of emitting the source block's URI, which is how the
        normalization reaches the wire rather than being discarded.
        """
        if not data_uri:
            return data_uri
        return self._normalized_by_hash.get(content_hash(data_uri), data_uri)


def admission_from_state(state: "dict[str, object] | None") -> ImageAdmission:
    """The request's arbiter, taken from the tool-state dict every PipesHub
    tool already receives (`AgentContext._seed_tool_state` seeds it beside the
    budget it composes).

    Falls back to a permissive arbiter over whatever budget the state holds,
    so an entry point that predates this -- or a test that builds `tool_state`
    by hand -- behaves exactly as it did before rather than losing its images.
    This is the only place that fallback is constructed.
    """
    from app.utils.image_policy import permissive_policy

    state = state or {}
    budget = state.get("image_budget")
    if not isinstance(budget, ImageBudget):
        budget = None

    existing = state.get("image_admission")
    if isinstance(existing, ImageAdmission):
        # The state's budget is authoritative. A caller that replaced it after
        # the admission was seeded (a test, or an entry point that builds its
        # own) would otherwise leave the arbiter counting against a budget
        # nothing else debits, and the conversation ceiling would silently
        # stop applying.
        if budget is not None and budget is not existing.budget:
            existing.budget = budget
        return existing

    return ImageAdmission(
        permissive_policy(MAX_IMAGES_IN_CONVERSATION), budget=budget or ImageBudget(),
    )


__all__ = [
    "MAX_ASPECT_RATIO",
    "MAX_IMAGES_IN_CONVERSATION",
    "MIN_AREA_PX",
    "MIN_SHORT_EDGE_PX",
    "AdmissionResult",
    "ImageBudget",
    "DegradeReason",
    "DegradedImage",
    "ImageAdmission",
    "ImageCandidate",
    "ImageOrigin",
    "admission_from_state",
    "content_hash",
]
