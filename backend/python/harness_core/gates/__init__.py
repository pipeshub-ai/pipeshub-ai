"""Run gates (G-03 dry run, G-04 plan approval, G-05 outcome verification), pinned as ``RunSpec.gates``.

Each is enforced by the harness where the thing it governs happens, never by the prompt: a dry run where calls are
executed (``EffectRunner``, under both the loop and the Tool Gateway), the plan gate in the approval policy both use,
verification in the kernel as the run says it is done. Kept import-light: ``harness_core.effects`` imports
``dry_run`` from here.
"""
