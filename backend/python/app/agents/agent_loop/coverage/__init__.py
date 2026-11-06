"""The coverage loop (``COVERAGE_LOOP``): before a knowledge answer is final,
every part of the question has evidence the model was shown, or a search run
in code has looked for it. See ``app.utils.coverage_switches`` for the
switches and ``hooks/coverage_gate.py`` for where a draft is checked.
"""
