"""Unit tests for app.agents.actions.response_transformer."""

from app.agents.actions.response_transformer import ResponseTransformer


def test_clean_returns_original_when_no_rules():
    data = {"id": 1, "name": "Test"}

    assert ResponseTransformer(data).clean() == data


def test_remove_exact_and_nested_paths_from_dicts_and_lists():
    data = {
        "expand": True,
        "issues": [
            {
                "id": "ISSUE-1",
                "self": "https://jira.example/1",
                "fields": {
                    "summary": "Bug",
                    "status": {"name": "Open", "self": "/status/1"},
                },
            }
        ],
    }

    cleaned = ResponseTransformer(data).remove("expand", "self", "fields.status.self").clean()

    assert "expand" not in cleaned
    assert "self" not in cleaned["issues"][0]
    assert cleaned["issues"][0]["fields"]["status"] == {"name": "Open"}


def test_remove_supports_wildcard_suffix_prefix_and_middle():
    data = {
        "assignee": {
            "avatarUrls": {"48x48": "avatar.png"},
            "profile": {"url": "https://example.test/u/1"},
        },
        "reporter": {
            "avatarUrls": {"48x48": "reporter.png"},
            "profile": {"url": "https://example.test/u/2"},
        },
    }

    cleaned = (
        ResponseTransformer(data)
        .remove("*.avatarUrls", "assignee.*", "reporter.*.url")
        .clean()
    )

    assert cleaned == {"reporter": {"profile": {}}}


def test_keep_only_selected_fields_and_remove_still_wins():
    data = {
        "id": "ISSUE-1",
        "key": "ENG-1",
        "fields": {
            "summary": "Bug",
            "description": "Long text",
            "status": {"name": "Open", "id": "1"},
        },
    }

    cleaned = (
        ResponseTransformer(data)
        .keep("id", "fields", "fields.summary", "fields.status")
        .remove("fields.status.id")
        .clean()
    )

    assert cleaned == {
        "id": "ISSUE-1",
        "fields": {
            "summary": "Bug",
            "status": {"name": "Open"},
        },
    }


def test_clean_preserves_primitive_list_items():
    data = ["a", 1, True, None]

    assert ResponseTransformer(data).remove("unused").clean() == data


def test_path_matches_suffix_prefix_and_middle_segments():
    transformer = ResponseTransformer({})

    assert transformer._path_matches("fields.assignee.avatarUrls", "avatarUrls") is True
    assert transformer._path_matches("self.url", "self") is True
    assert transformer._path_matches("fields.self.url", "self") is True
    assert transformer._path_matches("fields.status.name", "summary") is False
