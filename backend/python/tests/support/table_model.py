"""A fake chat model for table-description tests, and the synthetic rows it answers about.

It reads the rows out of the prompt it is sent and answers in the shape that prompt asks for,
so a scenario (skip, merge, duplicate, reorder, empty, long, malformed, out of range) is written
once and runs over any prompt revision and any parser that describes rows.
"""

from __future__ import annotations

import hashlib
import json
import re
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

HEAD = ["Name", "Offset", "Reset"]


def make_rows(count: int) -> list[list[str]]:
    return [[f"REG{i}", f"0x{i * 4:03X}", "0"] for i in range(count)]


class FakeTableModel:
    """Reads the keyed rows from the prompt and answers as ``behave(kind, ids, call_number)`` says.

    ``kind`` is "combined" (summary + headers + rows), "summary" (summary + headers only) or
    "rows". ``ids`` are the row ids sent: ``REG<n>``, or ``HDR`` for the header row. The answer
    copies each row's key unless the behaviour says the model got it wrong.
    """

    model_name = "fake-table-model"

    def __init__(
        self,
        behave: Callable[[str, list[str], int], str] | None = None,
        *,
        header_row_count: int = 0,
        headers: list[str] | None = None,
        describe: Callable[[str], str] | None = None,
        identify: Callable[[Any], str] | None = None,
        summary: str = "Register map",
        stop: BaseException | None = None,
    ) -> None:
        self.summary = summary
        #: Raised as is for a "nonretryable" behaviour, so a test can check it is the very exception the caller sees.
        self.stop = stop
        self.identify = identify
        self.describe = describe or (lambda rid: f"desc of {rid}")
        self.behave = behave or (lambda _kind, _ids, _n: "ok")
        self.header_row_count = header_row_count
        self.headers = HEAD if headers is None else headers
        self.calls: list[tuple[str, int]] = []
        self.reflections = 0

    @property
    def row_calls(self) -> list[int]:
        return [n for kind, n in self.calls if kind == "rows"]

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.calls if k == kind)

    @property
    def invocations(self) -> int:
        """Every request the model received: its calls and the reflections that followed a bad reply."""
        return len(self.calls) + self.reflections

    def _rows(self, text: str) -> list[tuple[str, str]]:
        """``(key, id)`` per keyed row of the prompt, in the order shown.

        The id is ``REG<n>`` found in the row's text, or ``HDR``; ``identify`` replaces that for
        tables whose cells do not carry such a token (it is given the row's parsed JSON).
        """
        starts = [i for i in (text.find(m) for m in ("Table Data (", "Rows Data (")) if i >= 0]
        if not starts:
            return []
        start = min(starts)
        end = text.find("Produce a single JSON object")
        if end < 0:
            end = text.find("Respond with ONLY")
        parts = re.split(r"(?m)^(?:Row \d+ )?\[([A-Z2-7]{4})\]: ", text[start:end])[1:]
        rows = []
        for key, body in zip(parts[0::2], parts[1::2]):
            if self.identify is not None:
                try:
                    rows.append((key, self.identify(json.JSONDecoder().raw_decode(body.lstrip())[0])))
                except ValueError:
                    rows.append((key, "HDR"))
            else:
                rows.append((key, m.group(0) if (m := re.search(r"REG\d+", body)) else "HDR"))
        return rows

    def _entries(self, behavior: str, rows: list[tuple[str, str]]) -> list[tuple[Any, Any]] | str:
        entries: list[tuple[Any, Any]] = [(key, self.describe(rid)) for key, rid in rows]
        ids = [rid for _, rid in rows]
        name, _, arg = behavior.partition(":")
        k = int(arg) if arg else 0
        if name == "ok":
            return entries
        if name == "skip":
            return entries[:k] + entries[k + 1 :]
        if name == "merge":
            merged = (entries[k][0], f"{self.describe(ids[k])} and {self.describe(ids[k + 1])}")
            return [*entries[:k], merged, *entries[k + 2 :]]
        if name == "dup":
            return [*entries[: k + 1], (entries[k][0], f"WRONG duplicate of {ids[k]}"), *entries[k + 1 :]]
        if name == "reverse":
            return entries[::-1]
        if name == "nothing":
            return []
        if name == "long":  # extra entries for keys the batch never had
            return [*entries, *[(f"ZZ{j:02d}", "EXTRA") for j in range(30)]]
        if name == "foreign":  # keys copied from some other batch
            return [(f"Q{j:03d}"[:4], text) for j, (_, text) in enumerate(entries)]
        if name == "malformed":
            return "this is not json {"
        if name == "counted":  # a model that counts rows instead of copying keys: "1".."n", shifted by k
            return [(str(i + 1 + k), text) for i, (_, text) in enumerate(entries)]
        if name == "partial":  # the first k rows copy their keys, the rest are numbered
            return [*entries[:k], *[(str(i + 1), text) for i, (_, text) in enumerate(entries) if i >= k]]
        if name == "skipcount":  # the first k rows copy their keys, row k is skipped, the rest are numbered
            return [*entries[:k], *[(str(i), text) for i, (_, text) in enumerate(entries) if i > k]]
        if name == "dictdesc":  # row k's description is an object, not text
            return [*entries[:k], (entries[k][0], {"text": f"desc of {ids[k]}"}), *entries[k + 1 :]]
        if name == "badkey":  # row k's key is a list, not text
            return [*entries[:k], ([entries[k][0]], entries[k][1]), *entries[k + 1 :]]
        if name == "lowerkeys":
            return [(key.lower(), text) for key, text in entries]
        if name == "bracketed":
            return [(f"[{key}]", text) for key, text in entries]
        if name == "dropmany":  # the first k rows go unanswered
            return entries[k:]
        if name == "keepfirst":  # only the first k rows are answered
            return entries[:k]
        raise AssertionError(behavior)

    async def ainvoke(self, messages: list, **_kwargs: object) -> SimpleNamespace:
        text = messages[-1].content
        kind = "combined" if '"summary"' in text and '"descriptions"' in text else "summary" if '"summary"' in text else "rows"
        rows = self._rows(text)
        ids = [rid for _, rid in rows]
        if not rows and kind != "summary" and "Respond only with valid JSON" in text:  # a reflection turn: the fake never repairs
            self.reflections += 1
            raise RuntimeError("the fake does not answer reflections")
        self.calls.append((kind, len(ids)))
        behavior = self.behave(kind, ids, len(self.calls))
        if behavior == "raise":
            raise RuntimeError("provider down")
        if behavior == "nonretryable":
            from app.exceptions.non_retryable import NonRetryableError

            raise self.stop or NonRetryableError("the request can never succeed as sent")
        payload: dict[str, object] = {}
        if kind == "summary":
            if behavior == "malformed":
                return SimpleNamespace(content="not json {", response_metadata={}, additional_kwargs={})
            payload = {"summary": self.summary, "headers": self.headers, "header_row_count": self.header_row_count}
        else:
            entries = self._entries(behavior, rows)
            if isinstance(entries, str):
                return SimpleNamespace(content=entries, response_metadata={}, additional_kwargs={})
            payload = {"descriptions": [{"key": key, "description": t} for key, t in entries]}
            if kind == "combined":
                payload |= {"summary": self.summary, "headers": self.headers, "header_row_count": self.header_row_count}
        return SimpleNamespace(content=json.dumps(payload), response_metadata={}, additional_kwargs={})


def by_first_row(first_id: str, behavior: str, *, size: int | None = None, kind: str = "rows") -> Callable[[str, list[str], int], str]:
    """``behavior`` for the batch of *kind* that starts at *first_id* (and has *size* rows); the rest answer well."""

    def behave(k: str, ids: list[str], _n: int) -> str:
        if k == kind and ids and ids[0] == first_id and (size is None or len(ids) == size):
            return behavior
        return "ok"

    return behave


def assert_aligned(result: object, count: int = 120, *, skip: frozenset[int] = frozenset()) -> None:
    """Row i's text is about REG<i> and about no other row, and is never a dict repr."""
    texts = texts_of(result)
    assert len(texts) == count
    for i, text in enumerate(texts):
        assert not text.lstrip().startswith("{"), f"row {i} is a dict repr: {text!r}"
        if i in skip:
            continue
        assert re.findall(r"REG\d+", text) == [f"REG{i}"], f"row {i} got {text!r}"




def texts_of(result: object) -> list[str]:
    """The descriptions of an enrichment result, or the list a parser's ``get_rows_text`` returned."""
    return list(result if isinstance(result, list) else result.descriptions)  # type: ignore[attr-defined]


_KEYED_ROW = re.compile(r"(?m)^(?:Row \d+ )?\[([A-Z2-7]{4})\]: ")


def keyed_rows(messages: list) -> list[tuple[str, Any]]:
    """``(key, row)`` for each keyed row shown in the last message of a prompt; the row is its parsed JSON."""
    text = "\n".join(str(getattr(m, "content", m)) for m in messages[-1:])
    starts = [i for i in (text.find(m) for m in ("Table Data (", "Rows Data (")) if i >= 0]
    if not starts:
        return []
    end = text.find("Produce a single JSON object")
    if end < 0:
        end = text.find("Respond with ONLY")
    parts = _KEYED_ROW.split(text[min(starts) : end])[1:]
    decoder = json.JSONDecoder()
    rows: list[tuple[str, Any]] = []
    for key, body in zip(parts[0::2], parts[1::2]):
        try:
            rows.append((key, decoder.raw_decode(body.lstrip())[0]))
        except ValueError:
            rows.append((key, None))
    return rows


def keyed_answer(messages: list, texts: list[str] | Callable[[int, Any], str]) -> SimpleNamespace:
    """A well-formed keyed answer for the rows in the prompt: ``texts`` is a list, or a function of (index, row)."""
    shown = keyed_rows(messages)
    pick = texts if callable(texts) else (lambda i, _row: texts[i] if i < len(texts) else "")
    return SimpleNamespace(
        descriptions=[SimpleNamespace(key=key, description=pick(i, row)) for i, (key, row) in enumerate(shown)],
    )


def count_hashed_bytes(monkeypatch: Any) -> list[int]:  # noqa: ANN401
    """Counts the bytes the key seed hashes (a one-item list, read after the call under test)."""
    from app.utils import table_enrichment

    total = [0]

    def sha256(data: bytes = b"") -> object:
        inner = hashlib.sha256()
        total[0] += len(data)
        inner.update(data)

        class Counting:
            def update(self, more: bytes) -> None:
                total[0] += len(more)
                inner.update(more)

            def hexdigest(self) -> str:
                return inner.hexdigest()

            def digest(self) -> bytes:
                return inner.digest()

        return Counting()

    monkeypatch.setattr(table_enrichment, "hashlib", SimpleNamespace(sha256=sha256))
    return total
