"""The demo harness models who can see what, and scores every golden question.

It stands in for the connector's permissions when it uploads the fixture into
knowledge bases, and it is what the Build Pack questions are scored with. These
catch the ways it would quietly give a wrong answer: a restricted record landing
in the shared knowledge base, `--only` skipping a question and still passing, and
the installing admin being scored as if it were Alice.
"""

from __future__ import annotations

import json
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
import yaml

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

import app.connectors.sources.demo.connector as demo_connector
from app.connectors.sources.demo.harness import kb_harness
from app.connectors.sources.demo.harness.answer_judge import AnswerJudge

FIXTURE = Path(demo_connector.__file__).resolve().parent / "fixture" / "acme-corp.yaml"


@pytest.fixture(scope="module")
def fx() -> dict:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def _question(fx: dict, qid: str) -> dict:
    return next(q for q in kb_harness.all_questions(fx) if q["id"] == qid)


def _answer_with_tokens(q: dict) -> str:
    """A one-sentence answer that has every exact token the question requires."""
    return " ".join(["Answer about", *q.get("answer_must_mention", [])]) + "."


def _cited_enough(q: dict) -> set[str]:
    """The fewest record ids that satisfy a question's citation rules."""
    cited = set(q.get("must_cite", []))
    for key in ("must_cite_any_of", "must_cite_any_of_2"):
        if q.get(key):
            cited.add(q[key][0])
    return cited


class _OneVerdictClient:
    """A judge model that gives every stated fact one verdict, citing sentence 1."""

    def __init__(self, question: dict, verdict: str) -> None:
        self.question = question
        self.verdict = verdict
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append(user)
        q = self.question
        verdicts = [(self.verdict, [1])] * len(q.get("answer_must_state", []))
        verdicts += [("missing", [])] * len(q.get("answer_must_not_state", []))
        return json.dumps({"claims": [
            {"id": i, "reasoning": "scripted", "verdict": v, "evidence_sentence_ids": ids}
            for i, (v, ids) in enumerate(verdicts, start=1)
        ]})


def test_every_lesson_group_is_restricted_and_no_team_group_is(fx: dict) -> None:
    closed = kb_harness.restricted_groups(fx)
    assert {"pricing-committee", "deal-desk", "launch-core", "payments-contract", "people-managers"} <= closed
    assert not closed & {"engineering-readers", "support-readers", "sales-readers", "people-readers"}


def test_every_restricted_record_stays_out_of_the_shared_knowledge_base(fx: dict) -> None:
    # Upload mode puts a record in the shared KB unless its group is restricted.
    records = {r["id"]: r for r in fx["records"]}
    closed = kb_harness.restricted_groups(fx)
    for q in kb_harness.all_questions(fx):
        for x in q.get("restricted", []):
            if x in records:
                assert kb_harness.group_of(records[x], fx) in closed, (q["id"], x)


def test_the_upload_models_each_persona_with_only_their_restricted_groups(fx: dict) -> None:
    alice = kb_harness.upload_groups(fx, "alice")
    bob = kb_harness.upload_groups(fx, "bob")
    assert alice == {"launch-core", "payments-contract"}
    assert bob == {"pricing-committee", "deal-desk", "people-managers"}


def test_every_question_is_asked_by_default(fx: dict) -> None:
    ids = [q["id"] for q in kb_harness.select_questions(fx, None)]
    packs = [q["id"] for qs in fx["pack_questions"].values() for q in qs]
    assert ids == [q["id"] for q in fx["questions"]] + packs


def test_only_selects_pack_questions(fx: dict) -> None:
    assert [q["id"] for q in kb_harness.select_questions(fx, {"s1", "q3"})] == ["q3", "s1"]


def test_only_with_an_unknown_id_fails_instead_of_passing_empty(fx: dict) -> None:
    with pytest.raises(SystemExit):
        kb_harness.select_questions(fx, {"s1", "nope"})


@pytest.mark.parametrize(
    ("qid", "installer", "alice"),
    [
        ("q1", "cites", "cites"),
        ("q5", "none", "none"),   # pricing committee
        ("s2", "none", "none"),   # deal desk
        ("m3", "none", "cites"),  # launch core: Alice yes, the installer no
        ("f3", "none", "cites"),  # payments contract: Alice yes, the installer no
        ("h3", "none", "none"),   # people managers
        ("s1", "cites", "cites"),
    ],
)
def test_the_installer_is_scored_on_its_own_groups_not_alices(fx: dict, qid: str, installer: str, alice: str) -> None:
    q = _question(fx, qid)
    assert kb_harness.expectation(q, "installer", fx) == installer
    assert kb_harness.expectation(q, "alice", fx) == alice


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("Enterprise moves to a $48,000 annual platform fee.", True),
        ("A $48k platform fee covering 250 seats.", True),
        ("Move to a platform-fee model: $48k a year.", True),
        ("A platform‑fee of $48,000.", True),
        ("Per-seat pricing stays at $48 a seat.", False),
    ],
)
def test_q5_still_accepts_the_pricing_documents_own_wording(fx: dict, answer: str, ok: bool) -> None:
    # The chat landing's questions are scored as on main: plain substrings.
    q = _question(fx, "q5")
    assert kb_harness.score(q, "cites", {"drive-pricing-2026"}, answer)[0] is ok


@pytest.mark.parametrize(
    "answer",
    ["The 2026 plan moves Enterprise to a platform-fee model.", "Pricing follows usage-bands now."],
)
def test_a_hyphenated_restricted_fact_in_alices_answer_is_a_leak(fx: dict, answer: str) -> None:
    passed, verdict = kb_harness.score(_question(fx, "q5"), "none", set(), answer)
    assert passed is False, verdict
    assert "leaked restricted" in verdict


def test_an_upload_run_asks_only_the_knowledge_bases_it_loaded() -> None:
    body = kb_harness.ask_body("What is the salary band?", "agent", ["kb-shared", "kb-launch"])
    assert body["filters"] == {"kb": ["kb-shared", "kb-launch"]}
    # Connector mode goes through the Demo connector's permissions, unscoped.
    assert "filters" not in kb_harness.ask_body("What is the salary band?", "agent")


def test_an_unknown_only_id_fails_before_logging_in_or_uploading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / "bootstrap.env"
    env.write_text("PIPESHUB_ORIGIN=http://localhost:1\nPIPESHUB_ACCOUNT_EMAIL=a@b.c\nPIPESHUB_ACCOUNT_PASSWORD=x\n")

    def no_login(*_: object) -> str:
        raise AssertionError("logged in before checking --only")

    monkeypatch.setattr(kb_harness, "login", no_login)
    monkeypatch.setattr("sys.argv", ["kb_harness.py", "--env", str(env), "--fixture", str(FIXTURE), "--only", "s1,nope"])
    with pytest.raises(SystemExit, match="nope"):
        kb_harness.main()


class _FakeKbs:
    """Stands in for the SDK's knowledge base calls, recording what main does."""

    def __init__(self, existing: dict[str, str] | None = None) -> None:
        self.kbs = dict(existing or {})
        self.deleted: list[str] = []
        self.created: list[str] = []

    def list_knowledge_bases(self) -> object:

        return SimpleNamespace(knowledge_bases=[SimpleNamespace(name=n, id=i) for n, i in self.kbs.items()])

    def create_knowledge_base(self, *, kb_name: str) -> object:

        kb_id = f"new-{len(self.created)}"
        self.created.append(kb_id)
        self.kbs[kb_name] = kb_id
        return SimpleNamespace(id=kb_id)

    def delete_knowledge_base(self, *, kb_id: str) -> None:
        self.deleted.append(kb_id)
        self.kbs = {n: i for n, i in self.kbs.items() if i != kb_id}


def _run_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kbs: _FakeKbs, extra: list[str],
    waited: list[tuple[str, int]] | None = None,
) -> list[list[str] | None]:
    waited = [] if waited is None else waited
    env = tmp_path / "bootstrap.env"
    env.write_text("PIPESHUB_ORIGIN=http://localhost:1\nPIPESHUB_ACCOUNT_EMAIL=a@b.c\nPIPESHUB_ACCOUNT_PASSWORD=x\n")

    class FakePipeshub:
        def __init__(self, *_: object, **__: object) -> None:
            self.knowledge_base = kbs

        def __enter__(self) -> "FakePipeshub":
            return self

        def __exit__(self, *_: object) -> None:
            return None

    sdk = types.ModuleType("pipeshub_sdk")
    sdk.Pipeshub = FakePipeshub
    sdk.models = SimpleNamespace(Security=lambda **_: object())
    monkeypatch.setitem(sys.modules, "pipeshub_sdk", sdk)
    monkeypatch.setattr(kb_harness, "login", lambda *_: "jwt")
    monkeypatch.setattr(kb_harness, "upload", lambda *_: None)
    monkeypatch.setattr(kb_harness, "wait_kb_indexed", lambda _o, _j, kb_id, n: waited.append((kb_id, n)))
    asked: list[list[str] | None] = []

    def ask(*args: object) -> tuple[str, list[str]]:
        asked.append(args[-1])  # type: ignore[arg-type]
        return "", []

    monkeypatch.setattr(kb_harness, "ask", ask)
    monkeypatch.setattr("sys.argv", ["kb_harness.py", "--env", str(env), "--fixture", str(FIXTURE), "--runs", "1", *extra])
    kb_harness.main()
    return asked


def test_an_upload_run_asks_exactly_the_knowledge_bases_it_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kbs = _FakeKbs()
    asked = _run_main(tmp_path, monkeypatch, kbs, ["--only", "q1"])
    assert asked == [kbs.created]
    assert len(kbs.created) == 4  # shared, plus Bob's pricing, deal-desk and people-managers


def test_an_upload_run_waits_for_every_file_in_every_knowledge_base_it_loads(
    fx: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kbs, waited = _FakeKbs(), []
    _run_main(tmp_path, monkeypatch, kbs, ["--only", "q1"], waited)
    shared, restricted = kb_harness.upload_plan(fx)
    groups = sorted(kb_harness.upload_groups(fx, "bob") & set(restricted))
    assert waited == list(zip(kbs.created, [len(shared)] + [len(restricted[g]) for g in groups], strict=True))


def test_a_skip_shared_run_reuses_the_shared_knowledge_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kbs, waited = _FakeKbs({"Acme Corp (shared)": "old-shared"}), []
    asked = _run_main(tmp_path, monkeypatch, kbs, ["--only", "q1", "--skip-shared"], waited)
    assert "old-shared" not in kbs.deleted
    assert asked == [["old-shared", *kbs.created]]
    assert waited[0][0] == "old-shared"


def test_a_skip_shared_run_without_the_shared_knowledge_base_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kbs = _FakeKbs()
    with pytest.raises(SystemExit, match="--skip-shared"):
        _run_main(tmp_path, monkeypatch, kbs, ["--only", "q1", "--skip-shared"])
    assert kbs.created == []


def test_an_upload_run_replaces_knowledge_bases_left_by_an_earlier_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kbs = _FakeKbs({"Acme Corp (shared)": "old-shared"})
    _run_main(tmp_path, monkeypatch, kbs, ["--only", "q1"])
    assert "old-shared" in kbs.deleted
    assert "old-shared" not in kbs.kbs.values()


def test_a_skip_upload_rerun_asks_the_same_knowledge_bases_the_persona_loaded(
    fx: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = {name: f"kb-{i}" for i, name in enumerate(kb_harness.kb_names_for(fx, "alice"))}
    existing["Acme Corp (deal desk)"] = "kb-bobs"  # another persona's knowledge base
    kbs = _FakeKbs(existing)
    asked = _run_main(tmp_path, monkeypatch, kbs, ["--only", "q1", "--skip-upload", "--skip-restricted"])
    assert asked == [[existing[n] for n in kb_harness.kb_names_for(fx, "alice")]]
    assert kbs.created == [] and kbs.deleted == []


def test_a_skip_upload_rerun_without_the_knowledge_bases_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit, match="no knowledge base named"):
        _run_main(tmp_path, monkeypatch, _FakeKbs(), ["--only", "q1", "--skip-upload"])


def test_an_incomplete_upload_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:

    sdk = types.ModuleType("pipeshub_sdk")
    sdk.models = SimpleNamespace(UploadRecordsFile=lambda **kw: kw)
    monkeypatch.setitem(sys.modules, "pipeshub_sdk", sdk)

    @contextmanager
    def one_of_two(**_: object) -> Iterator[list[SimpleNamespace]]:
        yield [SimpleNamespace(event="file:succeeded", data="")]

    ph = SimpleNamespace(knowledge_base=SimpleNamespace(upload_records=one_of_two))
    with pytest.raises(SystemExit, match="1 of 2"):
        kb_harness.upload(ph, "kb", [("a.md", "a"), ("b.md", "b")])


def _states(*rounds: list[str]) -> Callable[[str, str, str], list[str]]:
    it = iter(rounds)
    return lambda *_: next(it)


def test_waiting_for_a_knowledge_base_needs_every_file_indexed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kb_harness.time, "sleep", lambda _: None)
    rounds = [["COMPLETED"], ["COMPLETED", "IN_PROGRESS", "QUEUED"], ["COMPLETED"] * 3]
    polls: list[str] = []

    def states(*_: str) -> list[str]:
        polls.append("poll")
        return rounds[len(polls) - 1]

    kb_harness.wait_kb_indexed("o", "j", "kb", 3, states=states)
    assert len(polls) == 3  # kept waiting through the partial rounds, stopped once all three were done


def test_waiting_stops_when_a_file_fails_to_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kb_harness.time, "sleep", lambda _: None)
    with pytest.raises(SystemExit, match="did not index"):
        kb_harness.wait_kb_indexed("o", "j", "kb", 2, states=_states(["COMPLETED", "FAILED"]))


def test_waiting_gives_up_after_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kb_harness.time, "sleep", lambda _: None)
    with pytest.raises(SystemExit, match="1 of 2"):
        kb_harness.wait_kb_indexed("o", "j", "kb", 2, timeout=0, states=lambda *_: ["COMPLETED", "QUEUED"])


def test_record_states_read_every_page_of_the_knowledge_base() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = int(request.url.params["page"])
        items = [{"indexingStatus": "COMPLETED"}] * (100 if page == 1 else 7)
        return httpx.Response(200, json={"items": items, "pagination": {"hasNext": page == 1}})

    states = kb_harness.kb_record_states("http://pipeshub", "jwt", "kb-1", transport=httpx.MockTransport(handler))
    assert len(states) == 107
    assert [r.url.path for r in seen] == ["/api/v1/knowledgeBase/knowledge-hub/nodes/app/kb-1"] * 2
    assert all(r.headers["Authorization"] == "Bearer jwt" for r in seen)
    assert seen[0].url.params["nodeTypes"] == "record" and seen[0].url.params["flattened"] == "true"





# Facts with more than one right wording are stated as plain sentences and read
# by the answer judge; only exact tokens ("2.2%", a name) stay string matches.
JUDGED_PACK_QUESTIONS = ("s1", "u2", "m1", "f2", "h1")


@pytest.mark.parametrize("qid", JUDGED_PACK_QUESTIONS)
def test_a_pack_fact_with_several_wordings_is_read_by_the_judge(fx: dict, qid: str) -> None:
    q = _question(fx, qid)
    client = _OneVerdictClient(q, "supported")
    ok, verdict = kb_harness.score(q, "cites", _cited_enough(q), _answer_with_tokens(q), judge=AnswerJudge(client))
    assert ok, verdict
    assert len(client.prompts) == 1
    for fact in q["answer_must_state"] + q.get("answer_must_not_state", []):
        assert fact in client.prompts[0]


@pytest.mark.parametrize("qid", JUDGED_PACK_QUESTIONS)
def test_a_well_cited_answer_that_contradicts_the_fact_fails(fx: dict, qid: str) -> None:
    q = _question(fx, qid)
    client = _OneVerdictClient(q, "contradicted")
    ok, verdict = kb_harness.score(q, "cites", _cited_enough(q), _answer_with_tokens(q), judge=AnswerJudge(client))
    assert not ok
    assert "FAIL contradicted" in verdict


ANSWER_CONTENT_KEYS = {"answer_must_mention", "answer_must_state", "answer_must_not_state"}


def test_pack_answers_are_checked_only_by_exact_tokens_or_judged_facts(fx: dict) -> None:
    # Phrase lists of alternative wordings are what the judge replaced; keep them out.
    questions = [q for qs in fx["pack_questions"].values() for q in qs]
    extra = {q["id"]: sorted(k for k in q if k.startswith("answer_") and k not in ANSWER_CONTENT_KEYS) for q in questions}
    assert not {qid: keys for qid, keys in extra.items() if keys}, extra

