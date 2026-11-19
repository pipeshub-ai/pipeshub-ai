"""Residue units: every line of a code file that holds a word is in some unit.

code_units leaves out filler spans under 16 identifier characters (``import os``, ``TAX = 0.07``), so the keyword
index never held them and a path-free search that found nothing proved nothing. One residue unit per
file concatenates them; block units are unchanged, byte for byte.
"""
import re
from unittest.mock import MagicMock

import pytest

from app.models.blocks import BlocksContainer
from app.modules.parsers.code_parser import CodeFileParser
from app.modules.reconciliation.service import ReconciliationService
from app.modules.transformers.code_units import code_units, residue_pieces
from app.modules.transformers.transformer import ReconciliationContext
from app.modules.transformers.vectorstore import (
    _build_code_documents,
    _token_len,
    code_documents_for_record,
)

from .test_class_header_units import _code_blocks, _embedded, _record, _store

SRC = b'''import os

TAX = 0.07


def total(amount):
    return amount * (1 + TAX)


class Invoice:
    """An invoice."""

    def due(self):
        return os.environ.get("DUE", "30")
'''
CONTEXT = "client.py — src/client.py"


def _parse(src: bytes = SRC) -> BlocksContainer:
    return CodeFileParser().parse_to_blocks(src, "client.py", "src/client.py", "python")


def _residue(docs: list) -> list:
    return [d for d in docs if d.metadata.get("unitKind") == "residue"]


def test_one_residue_unit_per_file_holds_the_short_top_level_lines() -> None:
    container = _parse()
    docs = code_documents_for_record(_code_blocks(container), container.block_groups, "vr-1", "org-1", CONTEXT)
    [residue] = _residue(docs)
    assert residue.page_content.startswith(CONTEXT + "\n")
    assert "import os" in residue.page_content and "TAX = 0.07" in residue.page_content
    assert "def total" not in residue.page_content  # embedded as its own unit
    assert residue.metadata == {"virtualRecordId": "vr-1", "blockId": "vr-1_code_residue", "orgId": "org-1",
                                "isBlock": False, "isBlockGroup": False, "unitKind": "residue"}


def test_block_units_are_byte_identical() -> None:
    container = _parse()
    before = _build_code_documents(_code_blocks(container), "vr-1", "org-1", CONTEXT)
    after = code_documents_for_record(_code_blocks(container), container.block_groups, "vr-1", "org-1", CONTEXT)
    assert [(d.page_content, d.metadata) for d in after if d.metadata.get("unitKind") is None] == \
        [(d.page_content, d.metadata) for d in before]


def test_a_file_whose_every_line_is_in_a_symbol_has_no_residue() -> None:
    container = _parse(b"def helper():\n    return 1\n")
    assert not _residue(code_documents_for_record(_code_blocks(container), [], "vr-1", "org-1", CONTEXT))


def test_a_residue_longer_than_a_unit_is_split_and_no_line_is_lost() -> None:
    lines = [f"RATE_{i} = {i}" for i in range(400)]
    container = _parse(("\n\n\n".join(f"def f{i}():\n    return {i}\n\n\n{line}" for i, line in enumerate(lines))
                        ).encode())
    pieces = residue_pieces(_code_blocks(container), CONTEXT, token_len=_token_len,
                            split_long_line=lambda line, n: [line])
    assert len(pieces) > 1 and all(p.startswith(CONTEXT + "\n") for p in pieces)
    assert all(_token_len(p) <= 512 for p in pieces)
    assert [line for p in pieces for line in p.split("\n")[1:] if line] == lines


def test_a_filler_line_longer_than_a_unit_is_cut_between_words() -> None:
    """A word cut across two pieces is in neither as a whole word, so a keyword lookup would miss it."""
    line = "ab = cd + ef - gh * ij / kl % mn"  # 14 identifier characters: a filler span the residue keeps
    container = _parse(f"{line}\n\n\ndef helper():\n    return 1\n".encode())

    def hard_cut(text: str, n: int) -> list[str]:  # what the token splitter does: cuts anywhere
        return [text[i:i + n] for i in range(0, len(text), n)]

    pieces = residue_pieces(_code_blocks(container), "", token_len=len, split_long_line=hard_cut, budget=16)
    assert len(pieces) > 1 and all(len(p) <= 16 for p in pieces)
    assert [w for p in pieces for w in p.split()] == line.split()  # every word whole, in order


def test_a_word_a_long_symbol_line_is_cut_through_is_whole_in_the_residue() -> None:
    """A block line over a unit (SVG path data, a long literal) is cut by tokens, so a word can
    straddle two block units and be whole in neither; the file's residue holds every such word."""
    words = [f"segment{i}x{i * 7}" for i in range(1500)]
    container = _parse(f'def path():\n    return "{" ".join(words)}"\n'.encode())
    docs = code_documents_for_record(_code_blocks(container), [], "vr-1", "org-1", CONTEXT)
    whole = {w for d in docs if d.metadata.get("unitKind") is None for w in re.findall(r"\w+", d.page_content)}
    residue = {w for d in _residue(docs) for w in re.findall(r"\w+", d.page_content)}
    assert len([d for d in docs if d.metadata.get("unitKind") is None]) > 1  # the line was cut
    assert not [w for w in words if w not in whole and w not in residue]


def test_a_cut_blocks_words_are_carried_once_not_by_every_piece() -> None:
    """Carried by every piece, a long line's cut words would cost pieces x cuts to collect."""
    words = [f"segment{i}x{i * 7}" for i in range(1500)]
    container = _parse(f'def path():\n    return "{" ".join(words)}"\n'.encode())

    def hard_cut(text: str, n: int) -> list[str]:  # cuts anywhere, as the token splitter does
        return [text[i:i + n] for i in range(0, len(text), n)]
    units = code_units(_code_blocks(container), lambda _b: "", exceeds=lambda t, n: len(t) > n, token_len=len,
                       split_long_line=hard_cut, budget=200)
    assert len(units) > 2 and units[0].cut_words
    assert all(not u.cut_words for u in units[1:])


def test_a_word_longer_than_a_unit_is_not_added_to_the_residue() -> None:
    """A word longer than a unit (a base64 literal) is rebuilt whole from its pieces but fits no residue unit
    either, so it is not copied into the residue."""
    blob = "A" * 2000
    container = _parse(f'def data():\n    return "{blob}" + other_name\n'.encode())

    def hard_cut(text: str, n: int) -> list[str]:
        return [text[i:i + n] for i in range(0, len(text), n)]
    units = code_units(_code_blocks(container), lambda _b: "", exceeds=lambda t, n: len(t) > n, token_len=len,
                       split_long_line=hard_cut, budget=200)
    cut = [w for u in units for w in u.cut_words]
    assert blob in cut
    residue = residue_pieces([], "ctx", token_len=len, split_long_line=hard_cut, budget=200, cut_words=cut)
    assert all("A" * 100 not in piece for piece in residue)


def test_a_cut_word_is_kept_while_it_fits_the_residue_room_not_the_piece_budget() -> None:
    """The residue's room (the unit less its context line) is wider than a split block's piece budget (less
    its header and signature too): a word between the two is whole in the residue; one over the room is not."""
    from types import SimpleNamespace
    line = "." * 18 + "a" * 30 + "." * 8

    def hard_cut(text: str, n: int) -> list[str]:
        return [text[i:i + n] for i in range(0, len(text), n)]
    block = SimpleNamespace(index=0, data={"text": line}, parent_block_index=None,
                            code_metadata=SimpleNamespace(kind="function", signature="", docstring=""))
    [first, *_rest] = code_units([block], lambda _b: "", exceeds=lambda t, n: len(t) > n, token_len=len,
                                 split_long_line=hard_cut, budget=29)  # piece budget 28
    assert first.cut_words == ("a" * 30,)
    between, over = "a" * 30, "z" * 40  # header "ctx" leaves the residue a room of 36
    [piece] = residue_pieces([], "ctx", token_len=len, split_long_line=hard_cut, budget=40,
                             cut_words=[between, over, "short"])
    assert piece.split("\n")[1].split() == ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "short"]


def _cut_words(line: str, budget: int, signature: str = "") -> tuple[str, ...]:
    """A one-line block's cut words, with ``len`` as the token count and a cut every ``budget`` characters."""
    from types import SimpleNamespace

    def hard_cut(text: str, n: int) -> list[str]:
        return [text[i:i + n] for i in range(0, len(text), n)]
    block = SimpleNamespace(index=0, data={"text": line}, parent_block_index=None,
                            code_metadata=SimpleNamespace(kind="function", signature=signature, docstring=""))
    units = code_units([block], lambda _b: "", exceeds=lambda t, n: len(t) > n, token_len=len,
                       split_long_line=hard_cut, budget=budget)
    return tuple(w for u in units for w in u.cut_words)


def test_a_cut_word_spanning_three_pieces_is_rebuilt_whole_and_kept_while_it_fits_the_residue() -> None:
    """Unit budget 40 with a 7-character signature leaves pieces of 32 and the residue (header `ctx`) a room of
    36: a 34-character word cut across three pieces (1 + 32 + 1 characters) fits the residue whole."""
    word = "a" * 34
    assert _cut_words("." * 31 + word + "." * 20, 40, signature="def f()") == (word,)
    [piece] = residue_pieces([], "ctx", token_len=len, split_long_line=lambda t, n: [t], budget=40,
                             cut_words=[word])
    assert piece.split("\n")[1] == word


def test_cut_words_spanning_many_pieces_are_rebuilt_whole_each_once() -> None:
    first, second = "a" * 100, "b" * 61
    assert _cut_words("." * 5 + first + ".." + second + "." * 9, 29) == (first, second)


@pytest.mark.asyncio
async def test_a_reconcile_replaces_the_residue_even_when_only_a_short_line_changed() -> None:
    old, new = _parse(), _parse(SRC.replace(b"0.07", b"0.08"))
    service = ReconciliationService()
    to_index, to_delete, unchanged = service.compute_diff(service.build_metadata(old), service.build_metadata(new))
    service.apply_preserved_ids(new, unchanged)
    vs = _store()
    await vs.apply(MagicMock(record=_record(new), reconciliation_context=ReconciliationContext(
        blocks_to_index_ids=to_index, block_ids_to_delete=to_delete)))
    deleted = [call.args[0] for call in vs.delete_blocks_by_ids.call_args_list]
    assert {"vr-1_code_residue"} in deleted
    [residue] = _residue(_embedded(vs))
    assert "TAX = 0.08" in residue.page_content and "0.07" not in residue.page_content


@pytest.mark.asyncio
async def test_a_reconcile_that_only_removes_a_short_line_rebuilds_the_residue() -> None:
    old, new = _parse(), _parse(SRC.replace(b"TAX = 0.07\n", b""))
    service = ReconciliationService()
    to_index, to_delete, unchanged = service.compute_diff(service.build_metadata(old), service.build_metadata(new))
    service.apply_preserved_ids(new, unchanged)
    vs = _store()
    await vs.apply(MagicMock(record=_record(new), reconciliation_context=ReconciliationContext(
        blocks_to_index_ids=to_index, block_ids_to_delete=to_delete)))
    [residue] = _residue(_embedded(vs))
    assert "import os" in residue.page_content and "TAX = 0.07" not in residue.page_content
