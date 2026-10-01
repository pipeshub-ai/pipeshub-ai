## FRAMES benchmark — `20260930T173635Z-full-rag-s2b-91643e63`

Status: VALID

- Dataset `58d9fb6330f3`, split sha `fe4fe15c6965`, corpus `a89e6af19589`
- Snapshot 2024-10-15, git `5b6d47f0bbe5`
- Models: answerer=gpt-5.6-luna, judge_primary=claude-sonnet-5, judge_secondary=gemini-3.8-flash

### Board

| System | FRAMES acc % (95% CI) | Grounded acc % (95% CI) | Memory-suspect | Strict % | All gold in context % | Context recall % | Citation integrity % | ALCE recall % | Correct ∧ grounded % | p95 latency s | LLM calls / q | Input tok / q | Output tok / q | Cost / q | Cost / correct |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| adv-hybrid-expand-s2b | 78.9 (76.1–81.7) | 73.2 (70.1–76.2) | 5 (0.6%) | 78.0 (75.2–80.8) | 75.4 (72.5–78.3) | 90.4 (89.2–91.7) | 92.5 | 26.6 | 6.0 | 54.2 | 2.0 | 24,674 | 568 | $0.0056 | $0.0071 |
| adv-std-hybrid-expand-rerank-s2b | 72.5 (69.4–75.5) | 66.4 (63.1–69.7) | 14 (1.7%) | 71.7 (68.6–74.8) | 73.3 (70.3–76.3) | 89.7 (88.4–91.0) | 100.0 | 26.3 | 6.5 | 35.6 | 2.0 | 36,125 | 619 | $0.0080 | $0.0110 |

### By split (95% CI)

| Split | Metric | adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank-s2b |
|---|---|---|---|
| dev | FRAMES acc % | 79.5 (73.5–85.0) | 77.0 (71.0–82.5) |
| dev | Grounded acc % | 73.0 (66.5–79.0) | 73.5 (67.5–79.5) |
| dev | Memory-suspect % | 0.5 (0.0–1.5) | 0.5 (0.0–1.5) |
| heldout | FRAMES acc % | 78.7 (75.5–81.9) | 71.0 (67.5–74.5) |
| heldout | Grounded acc % | 73.2 (69.9–76.6) | 64.1 (60.4–67.8) |
| heldout | Memory-suspect % | 0.6 (0.2–1.3) | 2.1 (1.0–3.2) |

### Accuracy by reasoning type

| Label | adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank-s2b |
|---|---|---|
| Multiple constraints | 77.8 | 71.2 |
| Numerical reasoning | 72.7 | 65.2 |
| Post processing | 76.6 | 63.6 |
| Tabular reasoning | 75.4 | 67.8 |
| Temporal reasoning | 75.2 | 67.6 |

### Accuracy by gold-article count

| Label | adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank-s2b |
|---|---|---|
| 2 | 86.6 | 81.2 |
| 3 | 78.2 | 70.9 |
| 4 | 73.9 | 65.7 |
| 5+ | 62.0 | 57.6 |

### Evidence verification

Every answer the primary judge marked correct is checked against the context its system showed the answering model. *Memory-suspect*: judged correct, but the facts it depends on are not in that context. A system shown no context (closed book) lands every correct answer there.

| System | Supported | Partial | Memory-suspect | No evidence | Unparseable | Verified % |
|---|---|---|---|---|---|---|
| adv-hybrid-expand-s2b | 603 | 37 | 5 | 0 | 5 | 100.0 |
| adv-std-hybrid-expand-rerank-s2b | 547 | 35 | 14 | 0 | 1 | 100.0 |

Memory-suspect questions:

- **adv-hybrid-expand-s2b**: 27, 229, 310, 410, 699
- **adv-std-hybrid-expand-rerank-s2b**: 27, 76, 88, 142, 149, 215, 310, 358, 478, 520, 539, 645, 688, 821

### Paired comparisons (exact McNemar)

| A | B | A only | B only | p |
|---|---|---|---|---|
| adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank-s2b | 105 | 52 | 0.0000 |

### Paired comparisons on grounded correctness (exact McNemar)

| A | B | A only | B only | p |
|---|---|---|---|---|
| adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank-s2b | 122 | 66 | 0.0001 |

Judge agreement (Cohen's κ, primary vs secondary): adv-hybrid-expand-s2b: 0.975, adv-std-hybrid-expand-rerank-s2b: 0.970
