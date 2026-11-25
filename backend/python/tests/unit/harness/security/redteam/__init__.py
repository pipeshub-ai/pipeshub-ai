"""G2's red-team suite: injection planted in records an engine reads, measured from what the controls saw.

The verdict (``verdict.py``) is computed from observations only — connector effects, canary hits, egress
attempts, secrets seen, vendor keys reaching E3, artifacts published from outside ``out/``, changed inputs —
never from the model's text (G2-RT-2). The cases (``cases.py``) are data; the offline and live drivers run
them against the real controls (S1's Docker lane) and fill in what each control observed.
"""
