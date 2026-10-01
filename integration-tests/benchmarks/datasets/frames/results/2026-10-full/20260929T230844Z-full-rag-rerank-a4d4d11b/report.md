## FRAMES benchmark — `20260929T230844Z-full-rag-rerank-a4d4d11b`

Status: VALID

- Dataset `58d9fb6330f3`, split sha `fe4fe15c6965`, corpus `a89e6af19589`
- Snapshot 2024-10-15, git `5b6d47f0bbe5`
- Models: answerer=gpt-5.6-luna, judge_primary=claude-sonnet-5, judge_secondary=gemini-3.8-flash

### Board

| System | FRAMES acc % (95% CI) | Grounded acc % (95% CI) | Memory-suspect | Strict % | All gold in context % | Context recall % | Citation integrity % | ALCE recall % | Correct ∧ grounded % | p95 latency s | LLM calls / q | Input tok / q | Output tok / q | Cost / q | Cost / correct |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| adv-std-hybrid-decompose-rerank-s2b | 69.9 (66.6–73.1) | 63.0 (59.7–66.3) | 12 (1.5%) | 68.6 (65.4–71.7) | 71.1 (68.0–74.2) | 88.8 (87.5–90.1) | 100.0 | 26.0 | 4.8 | 24.3 | 2.0 | 36,718 | 608 | $0.0081 | $0.0115 |
| adv-std-hybrid-expand-rerank | 68.8 (65.7–72.0) | 62.3 (58.9–65.5) | 11 (1.3%) | 67.4 (64.1–70.5) | 73.4 (70.4–76.5) | 90.0 (88.8–91.3) | 100.0 | 35.1 | 8.2 | 43.1 | 2.0 | 19,246 | 668 | $0.0046 | $0.0068 |
| adv-hybrid-expand-rerank | 64.7 (61.4–67.8) | 56.8 (53.4–60.2) | 13 (1.6%) | 63.6 (60.2–66.9) | 74.2 (71.1–77.2) | 90.3 (89.1–91.6) | 78.8 | 38.2 | 9.0 | 108.8 | 2.0 | 5,744 | 686 | $0.0020 | $0.0030 |
| adv-hybrid-decompose-rerank-s2b | 63.7 (60.4–67.0) | 56.4 (53.0–59.8) | 10 (1.2%) | 62.6 (59.3–65.9) | 66.7 (63.5–69.9) | 86.7 (85.3–88.1) | 91.7 | 26.2 | 4.1 | 32.4 | 2.0 | 26,198 | 651 | $0.0060 | $0.0094 |
| adv-hybrid-decompose-rerank | 55.1 (51.6–58.5) | 47.8 (44.4–51.2) | 15 (1.8%) | 54.4 (50.8–57.8) | 67.4 (64.1–70.5) | 87.2 (85.8–88.5) | 79.0 | 37.0 | 6.6 | 144.8 | 2.0 | 6,072 | 682 | $0.0020 | $0.0037 |
| adv-hybrid-rerank | 46.2 (42.8–49.8) | 39.7 (36.3–43.1) | 18 (2.2%) | 45.9 (42.5–49.4) | 56.8 (53.4–60.2) | 81.2 (79.5–82.8) | 77.1 | 35.5 | 3.9 | 20.6 | 1.0 | 6,249 | 517 | $0.0019 | $0.0040 |

### By split (95% CI)

| Split | Metric | adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank | adv-hybrid-rerank | adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank |
|---|---|---|---|---|---|---|---|
| dev | FRAMES acc % | 56.0 (49.0–63.0) | 69.0 (62.5–75.5) | 69.0 (62.5–75.5) | 46.5 (39.5–53.5) | 75.5 (69.5–81.5) | 75.0 (69.0–81.0) |
| dev | Grounded acc % | 50.5 (43.5–57.5) | 60.0 (53.0–66.5) | 61.0 (54.0–67.5) | 40.0 (33.5–47.0) | 69.0 (62.5–75.0) | 70.5 (64.0–76.5) |
| dev | Memory-suspect % | 0.5 (0.0–1.5) | 1.0 (0.0–2.5) | 1.5 (0.0–3.5) | 1.5 (0.0–3.5) | 1.5 (0.0–3.5) | 1.0 (0.0–2.5) |
| heldout | FRAMES acc % | 54.8 (51.0–58.8) | 62.0 (58.2–65.9) | 63.3 (59.5–67.1) | 46.2 (42.1–50.2) | 68.1 (64.4–71.6) | 66.8 (63.1–70.5) |
| heldout | Grounded acc % | 47.0 (42.9–50.8) | 55.3 (51.4–59.3) | 55.4 (51.6–59.5) | 39.6 (35.7–43.4) | 61.1 (57.2–64.9) | 59.6 (55.8–63.5) |
| heldout | Memory-suspect % | 2.2 (1.1–3.4) | 1.3 (0.5–2.2) | 1.6 (0.6–2.7) | 2.4 (1.3–3.7) | 1.4 (0.6–2.4) | 1.4 (0.6–2.4) |

### Accuracy by reasoning type

| Label | adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank | adv-hybrid-rerank | adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank |
|---|---|---|---|---|---|---|
| Multiple constraints | 53.9 | 61.6 | 63.9 | 44.6 | 68.3 | 67.8 |
| Numerical reasoning | 48.1 | 60.8 | 57.7 | 41.0 | 62.8 | 62.8 |
| Post processing | 52.3 | 62.6 | 60.7 | 47.7 | 64.5 | 61.7 |
| Tabular reasoning | 47.0 | 58.9 | 59.7 | 39.4 | 63.6 | 59.3 |
| Temporal reasoning | 48.9 | 58.3 | 58.3 | 40.3 | 66.9 | 65.1 |

### Accuracy by gold-article count

| Label | adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank | adv-hybrid-rerank | adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank |
|---|---|---|---|---|---|---|
| 2 | 65.2 | 75.4 | 73.5 | 56.2 | 79.2 | 74.8 |
| 3 | 49.8 | 58.2 | 60.0 | 38.6 | 68.1 | 68.1 |
| 4 | 51.5 | 58.2 | 58.2 | 46.3 | 63.4 | 65.7 |
| 5+ | 42.4 | 48.9 | 58.7 | 35.9 | 53.3 | 55.4 |

### Evidence verification

Every answer the primary judge marked correct is checked against the context its system showed the answering model. *Memory-suspect*: judged correct, but the facts it depends on are not in that context. A system shown no context (closed book) lands every correct answer there.

| System | Supported | Partial | Memory-suspect | No evidence | Unparseable | Verified % |
|---|---|---|---|---|---|---|
| adv-hybrid-decompose-rerank | 394 | 44 | 15 | 0 | 1 | 100.0 |
| adv-hybrid-decompose-rerank-s2b | 465 | 49 | 10 | 0 | 1 | 100.0 |
| adv-hybrid-expand-rerank | 468 | 52 | 13 | 0 | 0 | 100.0 |
| adv-hybrid-rerank | 327 | 36 | 18 | 0 | 0 | 100.0 |
| adv-std-hybrid-decompose-rerank-s2b | 519 | 40 | 12 | 0 | 5 | 100.0 |
| adv-std-hybrid-expand-rerank | 513 | 43 | 11 | 0 | 0 | 100.0 |

Memory-suspect questions:

- **adv-hybrid-decompose-rerank**: 7, 68, 142, 229, 289, 310, 325, 341, 384, 540, 585, 688, 699, 728, 744
- **adv-hybrid-decompose-rerank-s2b**: 106, 172, 256, 310, 358, 401, 415, 688, 728, 748
- **adv-hybrid-expand-rerank**: 82, 161, 172, 229, 277, 282, 382, 401, 478, 596, 688, 699, 701
- **adv-hybrid-rerank**: 68, 115, 145, 172, 209, 285, 292, 302, 325, 388, 444, 621, 691, 699, 701, 705, 728, 759
- **adv-std-hybrid-decompose-rerank-s2b**: 3, 9, 51, 82, 161, 342, 358, 388, 401, 539, 596, 701
- **adv-std-hybrid-expand-rerank**: 9, 27, 76, 92, 340, 520, 540, 679, 702, 777, 821

### Paired comparisons (exact McNemar)

| A | B | A only | B only | p |
|---|---|---|---|---|
| adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | 32 | 103 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-rerank | 40 | 119 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-rerank | 106 | 33 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-decompose-rerank-s2b | 42 | 164 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-expand-rerank | 42 | 155 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank | 86 | 94 | 0.6020 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-rerank | 169 | 25 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-decompose-rerank-s2b | 47 | 98 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 65 | 107 | 0.0017 |
| adv-hybrid-expand-rerank | adv-hybrid-rerank | 182 | 30 | 0.0000 |
| adv-hybrid-expand-rerank | adv-std-hybrid-decompose-rerank-s2b | 82 | 125 | 0.0034 |
| adv-hybrid-expand-rerank | adv-std-hybrid-expand-rerank | 76 | 110 | 0.0153 |
| adv-hybrid-rerank | adv-std-hybrid-decompose-rerank-s2b | 29 | 224 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-expand-rerank | 28 | 214 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 68 | 59 | 0.4779 |

### Paired comparisons on grounded correctness (exact McNemar)

| A | B | A only | B only | p |
|---|---|---|---|---|
| adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | 38 | 109 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-rerank | 44 | 118 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-rerank | 106 | 39 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-decompose-rerank-s2b | 39 | 164 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-expand-rerank | 49 | 168 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank | 93 | 96 | 0.8844 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-rerank | 168 | 30 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-decompose-rerank-s2b | 52 | 106 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 69 | 117 | 0.0005 |
| adv-hybrid-expand-rerank | adv-hybrid-rerank | 173 | 32 | 0.0000 |
| adv-hybrid-expand-rerank | adv-std-hybrid-decompose-rerank-s2b | 84 | 135 | 0.0007 |
| adv-hybrid-expand-rerank | adv-std-hybrid-expand-rerank | 80 | 125 | 0.0020 |
| adv-hybrid-rerank | adv-std-hybrid-decompose-rerank-s2b | 27 | 219 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-expand-rerank | 31 | 217 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 69 | 63 | 0.6636 |

Judge agreement (Cohen's κ, primary vs secondary): adv-hybrid-decompose-rerank: 0.971, adv-hybrid-decompose-rerank-s2b: 0.979, adv-hybrid-expand-rerank: 0.979, adv-hybrid-rerank: 0.976, adv-std-hybrid-decompose-rerank-s2b: 0.974, adv-std-hybrid-expand-rerank: 0.958
