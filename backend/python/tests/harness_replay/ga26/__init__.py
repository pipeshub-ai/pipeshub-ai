"""GA-26 (G-49): the P0 journeys offline, on the mock harness, the VCR gateway and the seeded tenant.

    python -m tests.harness_replay.ga26 --record           # re-record every cassette from its scripted model
    python -m tests.harness_replay.ga26 --check            # exit 1 when a cassette no longer matches its journey
    pytest -m ga26 tests/harness_replay                   # the lane: replay each journey and check what it must show

See ``journeys.py`` for what each journey runs and ``test_ga26_p0_journeys.py`` for what it must show.
"""
