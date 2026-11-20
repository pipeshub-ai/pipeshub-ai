"""The measures and generators behind the weight-precision equivalence proof."""

import numpy as np
import pytest

from tests.support import embedding_equivalence as eq


def _gauss(rows: int, dim: int = 32, seed: int = 0) -> np.ndarray:
    return eq.unit(np.random.default_rng(seed).standard_normal((rows, dim)))


class TestCosine:
    def test_stats_of_known_pairs(self) -> None:
        a = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
        b = np.array([[1.0, 0.0], [1.0, 0.0], [1.0, 1.0]])
        stats = eq.cosine_stats(a, b)
        assert stats["mean"] == pytest.approx(2 / 3)
        assert stats["min"] == pytest.approx(0.0)
        assert stats["sd"] == pytest.approx(np.std([1, 0, 1], ddof=1))
        assert stats["n"] == 3

    def test_p1_sits_near_the_low_tail(self) -> None:
        a = _gauss(500)
        b = a.copy()
        b[0] = -b[0]
        stats = eq.cosine_stats(a, b)
        assert stats["min"] == pytest.approx(-1.0) and stats["p1"] > 0.9

    def test_scale_does_not_matter(self) -> None:
        a = _gauss(5)
        assert eq.cosine_rows(a, 7.0 * a) == pytest.approx(np.ones(5))


class TestRanking:
    def test_best_first_and_ties_go_to_the_lower_index(self) -> None:
        docs = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.6, 0.8]])
        ranked = eq.rank_documents(np.array([[1.0, 0.0]]), docs, 4)
        assert ranked[0].tolist() == [0, 1, 3, 2]

    def test_overlap_counts_the_shared_share_of_the_top_k(self) -> None:
        ref = np.array([[1, 2, 3, 4], [1, 2, 3, 4]])
        other = np.array([[4, 3, 2, 1], [1, 2, 9, 8]])
        assert eq.overlap_at_k(ref, other, 4).tolist() == [1.0, 0.5]
        assert eq.overlap_at_k(ref, other, 2).tolist() == [0.0, 1.0]

    def test_top1_agreement(self) -> None:
        assert eq.top1_agreement(np.array([[1, 2], [3, 4]]), np.array([[1, 9], [4, 3]])).tolist() == [1.0, 0.0]

    def test_hit_at_k_uses_any_accepted_answer(self) -> None:
        ranked = np.array([[5, 6, 7], [5, 6, 7]])
        gold = [{7, 99}, {1}]
        assert eq.hit_at_k(ranked, gold, 3).tolist() == [1.0, 0.0]
        assert eq.hit_at_k(ranked, gold, 2).tolist() == [0.0, 0.0]


class TestBootstrap:
    def test_is_deterministic_for_a_seed(self) -> None:
        x = np.random.default_rng(1).random(200)
        assert eq.paired_bootstrap_ci(x, seed=3) == eq.paired_bootstrap_ci(x, seed=3)
        assert eq.paired_bootstrap_ci(x, seed=3) != eq.paired_bootstrap_ci(x, seed=4)

    def test_interval_brackets_the_mean_and_shrinks_with_more_queries(self) -> None:
        rng = np.random.default_rng(2)
        small = eq.paired_bootstrap_ci(rng.random(40))
        large = eq.paired_bootstrap_ci(rng.random(4000))
        assert small[1] <= small[0] <= small[2]
        assert (large[2] - large[1]) < (small[2] - small[1])

    def test_pairing_removes_between_query_spread(self) -> None:
        rng = np.random.default_rng(3)
        base = rng.random(500) * 100  # queries differ wildly
        other = base + 0.01
        mean, lo, hi = eq.paired_bootstrap_ci(other, base)
        assert mean == pytest.approx(0.01) and hi - lo < 1e-9
        unpaired = eq.paired_bootstrap_ci(other)
        assert unpaired[2] - unpaired[1] > 1.0

    def test_interval_covers_zero_for_pure_noise(self) -> None:
        noise = np.random.default_rng(4).standard_normal(300)
        _, lo, hi = eq.paired_bootstrap_ci(noise)
        assert lo < 0 < hi


class TestScoreDrift:
    def test_identical_encoders_do_not_drift(self) -> None:
        q, d = _gauss(20, seed=1), _gauss(60, seed=2)
        drift = eq.score_drift(q, q, d, d, top=10)
        assert drift["max"] == 0.0 and drift["median_adjacent_gap_top10"] > 0

    def test_noise_shows_up(self) -> None:
        q, d = _gauss(20, seed=1), _gauss(60, seed=2)
        drift = eq.score_drift(q, eq.degrade(q, 0.3, 5), d, eq.degrade(d, 0.3, 6), top=10)
        assert drift["p99"] > 0.01

    def test_rank_flip_rate(self) -> None:
        reference_gold, reference_rival = np.array([0.9, 0.9, 0.5, 0.5]), np.array([0.8, 0.8, 0.6, 0.6])
        gold, rival = np.array([0.9, 0.7, 0.5, 0.7]), np.array([0.8, 0.8, 0.6, 0.6])
        assert eq.rank_flip_rate(gold, rival, reference_gold, reference_rival) == pytest.approx(0.5)


class TestMixedIndex:
    def test_identical_copies_have_no_gap_and_tie_at_one_half(self) -> None:
        q, d = _gauss(30, seed=1), _gauss(80, seed=2)
        result = eq.mixed_index_pairs(q, d, d.copy(), top=10)
        assert result.gap_mean == 0.0 and result.gap_p99 == 0.0 and result.new_outranks_old == 0.5
        assert result.pairs == 300

    def test_a_biased_new_copy_moves_the_measures(self) -> None:
        # Control: if the new encoder favours something the old one does not, the measure must see it.
        q, d = _gauss(60, seed=1), _gauss(120, seed=2)
        biased_q = eq.biased(q, 0.5, seed=9)
        biased_d = eq.biased(d, 0.5, seed=9)
        result = eq.mixed_index_pairs(biased_q, d, biased_d, top=10)
        assert abs(result.new_outranks_old - 0.5) > 0.05 or abs(result.gap_mean) > 0.002

    def test_a_uniform_shift_of_new_scores_is_reported_as_a_gap(self) -> None:
        q, d = _gauss(30, seed=1), _gauss(80, seed=2)
        result = eq.mixed_index_pairs(q, d, eq.degrade(d, 0.05, 3), top=10)
        assert result.abs_gap_mean > 0

    def test_new_outranks_old_counts_the_new_copy_winning(self) -> None:
        q = np.array([[1.0, 0.0]])
        old = np.array([[0.6, 0.8], [0.0, 1.0]])
        new = np.array([[0.8, 0.6], [0.0, 1.0]])
        result = eq.mixed_index_pairs(q, old, new, top=1)
        assert result.new_outranks_old == 1.0 and result.gap_mean == pytest.approx(0.2)

    def test_random_split_is_seeded_and_close_to_even(self) -> None:
        a, b = eq.random_split(10_000, 7), eq.random_split(10_000, 7)
        assert (a == b).all() and not (a == eq.random_split(10_000, 8)).all()
        assert 0.47 < a.mean() < 0.53

    def test_split_index_takes_each_chunk_from_one_copy(self) -> None:
        old, new = np.zeros((4, 2)), np.ones((4, 2))
        mixed = eq.split_index(old, new, np.array([True, False, True, False]))
        assert mixed[:, 0].tolist() == [1, 0, 1, 0]


class TestControls:
    def test_degraded_vectors_fail_the_overlap_threshold(self) -> None:
        q, d = _gauss(50, seed=1), _gauss(400, seed=2)
        ref = eq.rank_documents(q, d, 10)
        worse = eq.rank_documents(eq.degrade(q, 1.5, 3), eq.degrade(d, 1.5, 4), 10)
        assert eq.overlap_at_k(ref, worse, 10).mean() < 0.95

    def test_tiny_noise_keeps_the_overlap_high(self) -> None:
        q, d = _gauss(50, seed=1), _gauss(400, seed=2)
        ref = eq.rank_documents(q, d, 10)
        near = eq.rank_documents(eq.degrade(q, 0.002, 3), eq.degrade(d, 0.002, 4), 10)
        assert eq.overlap_at_k(ref, near, 10).mean() > 0.97

    def test_outputs_stay_unit_length(self) -> None:
        d = _gauss(10)
        for out in (eq.degrade(d, 0.3, 1), eq.biased(d, 0.3, 1)):
            assert np.linalg.norm(out, axis=1) == pytest.approx(np.ones(10))


_WORDS = "retry backoff queue lease consumer partition offset checkpoint governor shard replica quorum ledger".split()


def _tree(tmp_path, files: int = 16, words: int = 600):  # noqa: ANN202
    rng = np.random.default_rng(0)
    for i in range(files):
        text = " ".join(rng.choice(_WORDS + [f"term{j}" for j in range(300)], size=words))
        (tmp_path / f"doc{i}.md").write_text(text)
    return tmp_path


class TestGenerators:
    def test_markdown_chunks_are_deterministic_and_seeded(self, tmp_path) -> None:
        root = _tree(tmp_path)
        a = eq.markdown_chunks(root, 10, seed=1)
        assert a == eq.markdown_chunks(root, 10, seed=1)
        assert a != eq.markdown_chunks(root, 10, seed=2)
        assert all(100 <= len(c.split()) <= 200 for c in a)

    def test_markdown_chunks_refuses_a_tree_that_is_too_small(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="need"):
            eq.markdown_chunks(_tree(tmp_path, files=1), 500, seed=1)

    def test_markdown_chunks_skip_vendored_directories(self, tmp_path) -> None:
        root = _tree(tmp_path)
        (root / "node_modules").mkdir()
        (root / "node_modules" / "x.md").write_text("zzzvendored " * 400)
        assert not any("zzzvendored" in c for c in eq.markdown_chunks(root, 20, seed=1))

    def test_number_heavy_chunks_cover_all_three_shapes(self) -> None:
        chunks = eq.number_heavy_chunks(9, seed=3)
        assert chunks == eq.number_heavy_chunks(9, seed=3) and chunks != eq.number_heavy_chunks(9, seed=4)
        assert chunks[0].startswith("id,code") and chunks[1].startswith("| region") and chunks[2].startswith("def ")

    def test_long_chunks_have_the_requested_length(self, tmp_path) -> None:
        sources = eq.markdown_chunks(_tree(tmp_path), 20, seed=1)
        longs = eq.long_chunks(sources, 5, seed=2)
        assert longs == eq.long_chunks(sources, 5, seed=2)
        assert all(len(c.split()) == 380 for c in longs)

    def test_near_duplicates_differ_slightly_from_their_source(self, tmp_path) -> None:
        chunks = eq.markdown_chunks(_tree(tmp_path), 20, seed=1)
        copies, sources = eq.near_duplicates(chunks, 5, seed=3)
        for copy, src in zip(copies, sources, strict=True):
            assert copy != chunks[src]
            shared = set(copy.split()) & set(chunks[src].split())
            assert len(shared) > 0.9 * len(set(chunks[src].split())) - 3

    def test_queries_are_deterministic_and_not_copies_of_their_chunk(self, tmp_path) -> None:
        chunks = eq.markdown_chunks(_tree(tmp_path), 30, seed=1)
        queries, gold = eq.paraphrase_queries(chunks, 15, seed=4)
        assert (queries, gold) == eq.paraphrase_queries(chunks, 15, seed=4)
        assert (queries, gold) != eq.paraphrase_queries(chunks, 15, seed=5)
        assert len(queries) == len(gold) > 0
        for query, i in zip(queries, gold, strict=True):
            first_sentence = chunks[i].split(".")[0].lower()
            assert query.lower() not in first_sentence and first_sentence not in query.lower()
            assert len(query.split()) < 20

    def test_a_query_names_terms_that_belong_to_its_chunk(self, tmp_path) -> None:
        chunks = eq.markdown_chunks(_tree(tmp_path), 30, seed=1)
        queries, gold = eq.paraphrase_queries(chunks, 10, seed=4)
        for query, i in zip(queries, gold, strict=True):
            hits = [w for w in query.lower().replace(",", "").split() if w in chunks[i].lower()]
            assert len(hits) >= 3

    def test_hard_negative_sets_include_the_gold_and_close_neighbours(self) -> None:
        docs = _gauss(10, dim=16)
        docs[7] = eq.unit(docs[3] + 0.01 * docs[5])
        sets = eq.hard_negative_sets(docs, [3, 0], threshold=0.95)
        assert sets[0] == {3, 7} and sets[1] == {0}

    def test_nearest_rival_is_never_the_gold_itself(self) -> None:
        docs = _gauss(10, dim=16)
        docs[7] = eq.unit(docs[3] + 0.01 * docs[5])
        assert eq.nearest_rival(docs, [3]).tolist() == [7]

    def test_corpus_hash_changes_with_any_text(self) -> None:
        assert eq.corpus_sha256(["a", "b"]) == eq.corpus_sha256(["a", "b"])
        assert eq.corpus_sha256(["a", "b"]) != eq.corpus_sha256(["a", "c"])
        assert eq.corpus_sha256(["ab", ""]) != eq.corpus_sha256(["a", "b"])
