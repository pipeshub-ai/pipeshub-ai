"""The connector-card item for folder patterns that matched nothing, and the dashboard's copy of the folder wording."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.connectors.core.base.connector.connector_service import (
    ActionItem,
    replace_action_item,
)
from app.connectors.core.registry.folder_filter_action_item import (
    FOLDER_FILTER_UNMATCHED_CODE,
    FOLDER_FILTER_UNMATCHED_TEXT,
    unmatched_folder_patterns_item,
)
from app.connectors.core.registry.folder_scope import (
    FOLDER_PATH_MESSAGES,
    folder_value_problems,
)

FRONTEND = Path(__file__).resolve().parents[7] / "frontend"
LOCALES = FRONTEND / "lib/i18n/locales"
CASES = FRONTEND / "app/(main)/workspace/connectors/utils/__tests__/folder-path-cases.json"


def _locale(name: str) -> dict:
    path = LOCALES / f"{name}.json"
    if not path.exists():
        pytest.skip("the frontend is not checked out next to the backend")
    return json.loads(path.read_text(encoding="utf-8"))


class TestUnmatchedPatternsItem:
    def test_one_pattern(self) -> None:
        item = unmatched_folder_patterns_item({"custmer/*/quotes"}, exclude=False)
        assert item.code == FOLDER_FILTER_UNMATCHED_CODE == "folder_filter_unmatched"
        assert item.severity == "warning"
        assert item.action is None
        assert item.title == "Folder pattern matched nothing"
        assert item.message == (
            'No folder this connector syncs matches "custmer/*/quotes", so nothing is synced for it. '
            "Check the spelling in the Folders filter."
        )
        assert item.params == {"count": "1", "values": '"custmer/*/quotes"'}

    def test_several_patterns_under_exclude(self) -> None:
        item = unmatched_folder_patterns_item({"b/*", "a/*"}, exclude=True)
        assert item.title == "2 folder patterns matched nothing"
        assert item.message.startswith('No folder this connector syncs matches "a/*", "b/*", so they exclude nothing.')
        assert item.params["context"] == "several_exclude"

    def test_names_at_most_five(self) -> None:
        item = unmatched_folder_patterns_item({f"p{i}/*" for i in range(7)}, exclude=False)
        assert item.params["values"] == '"p0/*", "p1/*", "p2/*", "p3/*", "p4/*", …'
        assert item.params["count"] == "7"


class TestReplaceActionItem:
    def test_sets_the_item_and_keeps_other_codes(self) -> None:
        other = ActionItem(code="other", title="t", message="m")
        connector = SimpleNamespace(last_action_items=[other, ActionItem(code="x", title="old", message="m")])
        new = ActionItem(code="x", title="new", message="m")
        replace_action_item(connector, "x", new)
        assert connector.last_action_items == [other, new]

    def test_none_clears_only_that_code_and_always_leaves_a_list(self) -> None:
        connector = SimpleNamespace(last_action_items=None)
        replace_action_item(connector, "x", None)
        assert connector.last_action_items == []
        other = ActionItem(code="other", title="t", message="m")
        connector.last_action_items = [other, ActionItem(code="x", title="t", message="m")]
        replace_action_item(connector, "x", None)
        assert connector.last_action_items == [other]


def _from_i18n(text: str) -> str:
    return re.sub(r"\{\{(\w+)\}\}", r"{\1}", text)


class TestTheDashboardSaysWhatTheServerSays:
    def test_the_unmatched_item_reads_the_same_in_en_us(self) -> None:
        known = _locale("en-US")["workspace"]["connectors"]["actionItems"]["known"][FOLDER_FILTER_UNMATCHED_CODE]
        for context, (title, message) in FOLDER_FILTER_UNMATCHED_TEXT.items():
            suffix = f"_{context}" if context else ""
            assert _from_i18n(known[f"title{suffix}"]) == title, context
            assert _from_i18n(known[f"message{suffix}"]) == message, context

    def test_every_locale_words_every_variant_with_the_same_values(self) -> None:
        source = _locale("en-US")["workspace"]["connectors"]["actionItems"]["known"][FOLDER_FILTER_UNMATCHED_CODE]
        for path in sorted(LOCALES.glob("*.json")):
            known = json.loads(path.read_text(encoding="utf-8"))["workspace"]["connectors"]["actionItems"]["known"]
            item = known[FOLDER_FILTER_UNMATCHED_CODE]
            assert item.keys() == source.keys(), path.name
            for key, text in item.items():
                assert set(re.findall(r"\{\{(\w+)\}\}", text)) == set(re.findall(r"\{\{(\w+)\}\}", source[key])), (
                    path.name, key,
                )

    def test_the_field_messages_read_the_same_in_en_us(self) -> None:
        errors = _locale("en-US")["workspace"]["connectors"]["filters"]["folderPaths"]["errors"]
        for code, message in FOLDER_PATH_MESSAGES.items():
            camel = re.sub(r"_(\w)", lambda m: m.group(1).upper(), code)
            assert _from_i18n(errors[camel]).replace("{value}", "{v}") == message, code

    def test_the_dashboard_checks_new_values_as_the_server_does(self) -> None:
        if not CASES.exists():
            pytest.skip("the frontend is not checked out next to the backend")
        cases = json.loads(CASES.read_text(encoding="utf-8"))
        assert len(cases) > 100
        for case in cases:
            problems = folder_value_problems(
                case["value"], exclude=case["exclude"], path_style=case["pathStyle"], wildcards=case["wildcards"]
            )
            assert problems == case["problems"], case
