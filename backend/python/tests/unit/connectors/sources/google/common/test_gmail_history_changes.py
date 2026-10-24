"""fold_history: the final state of each message a run of Gmail history touched."""

from app.connectors.sources.google.common.gmail_history_changes import fold_history


def ref(mid: str, labels: list[str] | None = None) -> dict:
    out = {"id": mid, "threadId": f"t-{mid}"}
    if labels is not None:
        out["labelIds"] = labels
    return out


def test_added_then_labelled_keeps_the_latest_labels() -> None:
    changes = fold_history([
        {"id": "12", "labelsAdded": [{"message": ref("m1", ["TRASH"]), "labelIds": ["TRASH"]}]},
        {"id": "10", "messagesAdded": [{"message": ref("m1", ["INBOX"])}]},
    ])
    assert changes.present["m1"].labels == ("TRASH",)
    assert not changes.present["m1"].in_scope
    assert changes.last_history_id == "12"


def test_deleted_wins_over_earlier_changes() -> None:
    changes = fold_history([
        {"id": "1", "messagesAdded": [{"message": ref("m1", ["INBOX"])}]},
        {"id": "2", "messagesDeleted": [{"message": ref("m1")}]},
        {"id": "3", "labelsRemoved": [{"message": ref("m1", []), "labelIds": ["UNREAD"]}]},
    ])
    assert "m1" in changes.deleted and "m1" not in changes.present


def test_message_only_in_messages_has_unknown_labels() -> None:
    changes = fold_history([{"id": "5", "messages": [ref("m2")]}])
    assert changes.present["m2"].labels is None
    assert changes.present["m2"].in_scope


def test_archived_message_stays_in_scope() -> None:
    changes = fold_history([{"id": "7", "labelsRemoved": [{"message": ref("m3", ["Label_9"]), "labelIds": ["INBOX"]}]}])
    assert changes.present["m3"].labels == ("Label_9",) and changes.present["m3"].in_scope


def test_empty_history() -> None:
    changes = fold_history([])
    assert changes.empty and changes.last_history_id is None
