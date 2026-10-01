## FRAMES benchmark — `20260930T190328Z-full-rag-rerank-bge-0d84582b`

Status: VALID

- Dataset `58d9fb6330f3`, split sha `fe4fe15c6965`, corpus `a89e6af19589`
- Snapshot 2024-10-15, git `5b6d47f0bbe5`
- Models: answerer=gpt-5.6-luna, judge_primary=claude-sonnet-5, judge_secondary=gemini-3.8-flash

### Board

| System | FRAMES acc % (95% CI) | Grounded acc % (95% CI) | Memory-suspect | Strict % | All gold in context % | Context recall % | Citation integrity % | ALCE recall % | Correct ∧ grounded % | p95 latency s | LLM calls / q | Input tok / q | Output tok / q | Cost / q | Cost / correct |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| adv-hybrid-expand-rerank-bge | 75.5 (72.6–78.4) | 69.7 (66.5–72.8) | 6 (0.7%) | 74.2 (71.1–77.2) | 78.9 (76.1–81.7) | 92.2 (91.1–93.3) | 78.6 | 39.5 | 10.2 | 328.5 | 2.0 | 6,932 | 673 | $0.0022 | $0.0029 |

### By split (95% CI)

| Split | Metric | adv-hybrid-expand-rerank-bge |
|---|---|---|
| dev | FRAMES acc % | 76.5 (70.5–82.0) |
| dev | Grounded acc % | 71.5 (65.0–77.5) |
| dev | Memory-suspect % | 0.5 (0.0–1.5) |
| heldout | FRAMES acc % | 75.2 (71.8–78.5) |
| heldout | Grounded acc % | 69.1 (65.4–72.6) |
| heldout | Memory-suspect % | 0.8 (0.2–1.6) |

### Accuracy by reasoning type

| Label | adv-hybrid-expand-rerank-bge |
|---|---|
| Multiple constraints | 75.0 |
| Numerical reasoning | 69.3 |
| Post processing | 72.9 |
| Tabular reasoning | 69.1 |
| Temporal reasoning | 74.8 |

### Accuracy by gold-article count

| Label | adv-hybrid-expand-rerank-bge |
|---|---|
| 2 | 82.1 |
| 3 | 74.0 |
| 4 | 72.4 |
| 5+ | 62.0 |

### Evidence verification

Every answer the primary judge marked correct is checked against the context its system showed the answering model. *Memory-suspect*: judged correct, but the facts it depends on are not in that context. A system shown no context (closed book) lands every correct answer there.

| System | Supported | Partial | Memory-suspect | No evidence | Unparseable | Verified % |
|---|---|---|---|---|---|---|
| adv-hybrid-expand-rerank-bge | 574 | 39 | 6 | 0 | 3 | 100.0 |

Memory-suspect questions:

- **adv-hybrid-expand-rerank-bge**: 259, 285, 388, 408, 469, 618

Judge agreement (Cohen's κ, primary vs secondary): adv-hybrid-expand-rerank-bge: 0.949
