# Regressions (T2)

One scenario per bug, review finding or generator disagreement.

**Rule: no fix merges without the scenario that fails before it.** Add the scenario first, watch the
conformance suite fail on it, then fix the code and watch it pass. A scenario here is never deleted to make a
suite pass.

Naming: `<id>.yaml`, one scenario per file, `id` unique across the corpus. The generator
(`tests/permissions/gen/`) shrinks a disagreeing world to a minimal one and writes it here as `GEN-<generator>-<seed>-<world>-<layer>`;
rename it to something that says what broke when you fix it.

The listed verdict is the **oracle's** answer for the failing pair. If the oracle is the part that is wrong,
fix the oracle in the same change and say why in the scenario's `comment`.
