from __future__ import annotations

from app.llm.prompt_cache.cache_key import build_prompt_cache_key


class TestBuildPromptCacheKey:
    def test_combines_org_and_user(self) -> None:
        key = build_prompt_cache_key(org_id="org-1", user_id="user-1")
        assert key
        assert len(key) <= 128

    def test_includes_spec_id_when_provided(self) -> None:
        without = build_prompt_cache_key(org_id="org-1", user_id="user-1")
        with_spec = build_prompt_cache_key(
            org_id="org-1", user_id="user-1", spec_id="agent-42"
        )
        assert with_spec != without

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

    def test_stays_within_max_length(self) -> None:
        key = build_prompt_cache_key(org_id="o" * 200, user_id="u" * 200)
        assert len(key) <= 128

    def test_colon_in_a_field_does_not_collide_with_a_split_tuple(self) -> None:
        # Colon-joining would make ("a:b", "c") and ("a", "b:c") identical.
        key_a = build_prompt_cache_key(org_id="a:b", user_id="c")
        key_b = build_prompt_cache_key(org_id="a", user_id="b:c")
        assert key_a != key_b

    def test_long_shared_prefix_does_not_drop_the_user_suffix(self) -> None:
        # Slicing a colon-joined key at 128 chars would keep only the org
        # and collide every user in that org.
        org = "o" * 200
        key_a = build_prompt_cache_key(org_id=org, user_id="user-a")
        key_b = build_prompt_cache_key(org_id=org, user_id="user-b")
        assert key_a != key_b
        assert len(key_a) <= 128
        assert len(key_b) <= 128
