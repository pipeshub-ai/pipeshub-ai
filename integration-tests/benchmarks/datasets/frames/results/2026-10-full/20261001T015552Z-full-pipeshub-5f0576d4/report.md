## FRAMES benchmark — `20261001T015552Z-full-pipeshub-5f0576d4`

Status: VALID

- Dataset `58d9fb6330f3`, split sha `fe4fe15c6965`, corpus `a89e6af19589`
- Snapshot 2024-10-15, git `5b6d47f0bbe5`
- Models: answerer=gpt-5.6-luna, judge_primary=claude-sonnet-5, judge_secondary=gemini-3.8-flash

### Board

| System | FRAMES acc % (95% CI) | Grounded acc % (95% CI) | Memory-suspect | Strict % | All gold in context % | Context recall % | Citation integrity % | ALCE recall % | Correct ∧ grounded % | p95 latency s | LLM calls / q | Input tok / q | Output tok / q | Cost / q | Cost / correct |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pipeshub | 92.7 (90.9–94.4) | 84.0 (81.4–86.4) | 11 (1.3%) | 91.6 (89.7–93.4) | 83.6 (81.1–86.2) | 94.4 (93.5–95.4) | 98.5 | 42.5 | 17.7 | 133.5 | 2.6 | 137,076 | 824 | $0.0167 | $0.0180 |
| oracle | 92.6 (90.8–94.3) | 89.2 (87.1–91.3) | 5 (0.6%) | 91.7 (89.8–93.6) | 99.8 (99.4–100.0) | 99.9 (99.8–100.0) | – | – | – | 6.2 | 1.0 | 23,167 | 229 | $0.0049 | $0.0053 |

### By split (95% CI)

| Split | Metric | oracle | pipeshub |
|---|---|---|---|
| dev | FRAMES acc % | 93.5 (90.0–96.5) | 94.0 (90.5–97.0) |
| dev | Grounded acc % | 90.5 (86.0–94.5) | 82.5 (77.0–87.5) |
| dev | Memory-suspect % | 0.5 (0.0–1.5) | 1.5 (0.0–3.5) |
| heldout | FRAMES acc % | 92.3 (90.1–94.4) | 92.3 (90.2–94.4) |
| heldout | Grounded acc % | 88.8 (86.2–91.2) | 84.5 (81.6–87.2) |
| heldout | Memory-suspect % | 0.6 (0.2–1.3) | 1.3 (0.5–2.2) |

### Accuracy by reasoning type

| Label | oracle | pipeshub |
|---|---|---|
| Multiple constraints | 93.3 | 93.4 |
| Numerical reasoning | 86.7 | 88.4 |
| Post processing | 88.8 | 83.2 |
| Tabular reasoning | 90.3 | 92.4 |
| Temporal reasoning | 89.9 | 90.6 |

### Accuracy by gold-article count

| Label | oracle | pipeshub |
|---|---|---|
| 2 | 92.3 | 93.3 |
| 3 | 93.3 | 90.9 |
| 4 | 93.3 | 97.0 |
| 5+ | 90.2 | 90.2 |

### Failure signatures

**pipeshub** (60 wrong answers)

- `F6_reasoning_miss`: 41
- `abstained`: 11
- `F5_links_unused`: 6
- `F1_stopped_early`: 2

### Evidence verification

Every answer the primary judge marked correct is checked against the context its system showed the answering model. *Memory-suspect*: judged correct, but the facts it depends on are not in that context. A system shown no context (closed book) lands every correct answer there.

| System | Supported | Partial | Memory-suspect | No evidence | Unparseable | Verified % |
|---|---|---|---|---|---|---|
| oracle | 735 | 21 | 5 | 0 | 2 | 100.0 |
| pipeshub | 692 | 59 | 11 | 0 | 2 | 100.0 |

Memory-suspect questions:

- **oracle**: 3, 27, 179, 342, 358
- **pipeshub**: 68, 190, 231, 256, 343, 344, 501, 536, 652, 756, 815

### Paired comparisons (exact McNemar)

| A | B | A only | B only | p |
|---|---|---|---|---|
| oracle | pipeshub | 29 | 30 | 1.0000 |

### Paired comparisons on grounded correctness (exact McNemar)

| A | B | A only | B only | p |
|---|---|---|---|---|
| oracle | pipeshub | 75 | 32 | 0.0000 |

Judge agreement (Cohen's κ, primary vs secondary): oracle: 0.957, pipeshub: 0.982
