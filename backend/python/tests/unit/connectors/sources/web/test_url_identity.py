"""Scenarios ID-01 to ID-08: which URLs name the same page."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.connectors.sources.web.url_identity import ALL_PARAMS, clean_url, is_same_page


class TestCleanUrl:
    @pytest.mark.parametrize(
        "url, expected",
        [
            ("https://a.test/p?utm_source=x&b=2&a=1#top", "https://a.test/p?b=2&a=1"),
            ("https://a.test/p?UTM_Medium=x", "https://a.test/p"),
            ("https://a.test/p?gclid=1&fbclid=2&msclkid=3", "https://a.test/p"),
            ("https://a.test/p?_ga=1&id=7", "https://a.test/p?id=7"),
            ("https://a.test/p#section", "https://a.test/p"),
            ("https://a.test/p?q=a%20b&utm_x=1", "https://a.test/p?q=a+b"),
        ],
    )
    def test_trackers_and_fragments_go(self, url: str, expected: str) -> None:
        assert clean_url(url) == expected

    def test_a_url_without_trackers_keeps_its_exact_query(self) -> None:
        url = "https://a.test/p?b=2&a=1&flag"
        assert clean_url(url) == url

    def test_a_parameter_that_merely_starts_like_a_tracker_stays(self) -> None:
        assert clean_url("https://a.test/p?utmost=1&gclidx=2") == "https://a.test/p?utmost=1&gclidx=2"

    def test_ignored_parameters_go_case_blind(self) -> None:
        url = "https://support.test/article/x.html?brand=Acme&product=ED&Language=en_US&id=3"
        assert clean_url(url, ["brand", "product", "language"]) == "https://support.test/article/x.html?id=3"

    def test_ignoring_every_parameter(self) -> None:
        assert clean_url("https://a.test/p?id=3&page=2", [ALL_PARAMS]) == "https://a.test/p"

    def test_variants_of_one_article_collapse(self) -> None:
        ignored = ["brand", "product", "language", "pills-nav"]
        variants = [
            "https://s.test/a/Intro.html?brand=R&product=ED&language=en_US",
            "https://s.test/a/Intro.html?brand=R&product=ED&language=en_US&pills-nav=surveys",
            "https://s.test/a/Intro.html?utm_campaign=x",
            "https://s.test/a/Intro.html",
        ]
        assert {clean_url(v, ignored) for v in variants} == {"https://s.test/a/Intro.html"}

    @given(st.text(alphabet="abcxyz_=&?#/%.-", max_size=40))
    def test_cleaning_twice_changes_nothing(self, tail: str) -> None:
        once = clean_url(f"https://a.test/{tail}", ["x"])
        assert clean_url(once, ["x"]) == once


class TestIsSamePage:
    @pytest.mark.parametrize(
        "canonical, page",
        [
            ("https://a.test/doc", "https://a.test/doc?ref=nav"),
            ("https://a.test/doc/", "https://a.test/doc"),
            ("HTTPS://A.TEST/doc", "https://a.test/doc"),
        ],
    )
    def test_same_path_is_the_same_page(self, canonical: str, page: str) -> None:
        assert is_same_page(canonical, page)

    @pytest.mark.parametrize(
        "canonical, page",
        [
            ("https://a.test/article-hub.html", "https://a.test/article/Intro.html"),
            ("https://b.test/doc", "https://a.test/doc"),
            ("http://a.test/doc", "https://a.test/doc"),
        ],
    )
    def test_another_path_or_host_is_not(self, canonical: str, page: str) -> None:
        assert not is_same_page(canonical, page)
