from __future__ import annotations

from app.llm.prompt_cache.cache_key import build_prompt_cache_key


class TestBuildPromptCacheKey:
    def test_combines_org_and_user(self) -> None:
        key = build_prompt_cache_key(org_id="org-1", user_id="user-1")
        assert key == "org-1:user-1"

    def test_includes_spec_id_when_provided(self) -> None:
        key = build_prompt_cache_key(org_id="org-1", user_id="user-1", spec_id="agent-42")
        assert key == "org-1:user-1:agent-42"

    def test_different_users_in_same_org_get_different_keys(self) -> None:
        key_a = build_prompt_cache_key(org_id="org-1", user_id="user-a")
        key_b = build_prompt_cache_key(org_id="org-1", user_id="user-b")
        assert key_a != key_b

    def test_same_org_and_user_is_stable_across_calls(self) -> None:
        key_1 = build_prompt_cache_key(org_id="org-1", user_id="user-1", spec_id="spec-a")
        key_2 = build_prompt_cache_key(org_id="org-1", user_id="user-1", spec_id="spec-a")
        assert key_1 == key_2

    def test_different_orgs_never_collide(self) -> None:
        key_a = build_prompt_cache_key(org_id="org-a", user_id="user-1")
        key_b = build_prompt_cache_key(org_id="org-b", user_id="user-1")
        assert key_a != key_b

    def test_truncates_to_max_length(self) -> None:
        key = build_prompt_cache_key(org_id="o" * 200, user_id="u" * 200)
        assert len(key) == 128
