# Combined FRAMES board

Pooled from: `20260929T183724Z-full-rag-625d2d4d`, `20260929T230844Z-full-rag-rerank-a4d4d11b`, `20260930T173635Z-full-rag-s2b-91643e63`, `20260930T190328Z-full-rag-rerank-bge-0d84582b`, `20261001T015552Z-full-pipeshub-5f0576d4`.

| System | From run |
|---|---|
| adv-dense-decompose | `20260929T183724Z-full-rag-625d2d4d` |
| adv-dense-expand | `20260929T183724Z-full-rag-625d2d4d` |
| adv-hybrid | `20260929T183724Z-full-rag-625d2d4d` |
| adv-hybrid-decompose | `20260929T183724Z-full-rag-625d2d4d` |
| adv-hybrid-decompose-rerank | `20260929T230844Z-full-rag-rerank-a4d4d11b` |
| adv-hybrid-decompose-rerank-s2b | `20260929T230844Z-full-rag-rerank-a4d4d11b` |
| adv-hybrid-expand | `20260929T183724Z-full-rag-625d2d4d` |
| adv-hybrid-expand-rerank | `20260929T230844Z-full-rag-rerank-a4d4d11b` |
| adv-hybrid-expand-rerank-bge | `20260930T190328Z-full-rag-rerank-bge-0d84582b` |
| adv-hybrid-expand-s2b | `20260930T173635Z-full-rag-s2b-91643e63` |
| adv-hybrid-rerank | `20260929T230844Z-full-rag-rerank-a4d4d11b` |
| adv-sparse | `20260929T183724Z-full-rag-625d2d4d` |
| adv-std-hybrid | `20260929T183724Z-full-rag-625d2d4d` |
| adv-std-hybrid-decompose-rerank-s2b | `20260929T230844Z-full-rag-rerank-a4d4d11b` |
| adv-std-hybrid-expand-rerank | `20260929T230844Z-full-rag-rerank-a4d4d11b` |
| adv-std-hybrid-expand-rerank-s2b | `20260930T173635Z-full-rag-s2b-91643e63` |
| closed_book | `20260929T183724Z-full-rag-625d2d4d` |
| naive-std | `20260929T183724Z-full-rag-625d2d4d` |
| naive_rag | `20260929T183724Z-full-rag-625d2d4d` |
| oracle | `20260929T183724Z-full-rag-625d2d4d` |
| pipeshub | `20261001T015552Z-full-pipeshub-5f0576d4` |

Questions refused by the provider for at least one system: none.

# View: all questions

## FRAMES benchmark — `all questions`

Status: VALID

### Board

| System | FRAMES acc % (95% CI) | Grounded acc % (95% CI) | Memory-suspect | Strict % | All gold in context % | Context recall % | Citation integrity % | ALCE recall % | Correct ∧ grounded % | p95 latency s | LLM calls / q | Input tok / q | Output tok / q | Cost / q | Cost / correct |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| oracle | 93.0 (91.1–94.7) | 90.0 (88.0–92.0) | 2 (0.2%) | 91.9 (89.9–93.7) | 99.8 (99.4–100.0) | 99.9 (99.8–100.0) | – | – | – | 6.3 | 1.0 | 23,167 | 234 | $0.0049 | $0.0053 |
| pipeshub | 92.7 (90.9–94.4) | 84.0 (81.4–86.4) | 11 (1.3%) | 91.6 (89.7–93.4) | 83.6 (81.1–86.0) | 94.4 (93.5–95.3) | 98.5 | 42.5 | 17.7 | 133.5 | 2.6 | 137,076 | 824 | $0.0167 | $0.0180 |
| adv-hybrid-expand-s2b | 78.9 (76.1–81.6) | 73.2 (70.1–76.2) | 5 (0.6%) | 78.0 (75.1–80.8) | 75.4 (72.5–78.3) | 90.4 (89.2–91.6) | 92.5 | 26.6 | 6.0 | 54.2 | 2.0 | 24,674 | 568 | $0.0056 | $0.0071 |
| adv-hybrid-expand-rerank-bge | 75.5 (72.6–78.3) | 69.7 (66.5–72.7) | 6 (0.7%) | 74.2 (71.1–77.1) | 78.9 (76.1–81.6) | 92.2 (91.1–93.3) | 78.6 | 39.5 | 10.2 | 328.5 | 2.0 | 6,932 | 673 | $0.0022 | $0.0029 |
| adv-hybrid-expand | 73.4 (70.4–76.5) | 67.5 (64.3–70.8) | 7 (0.8%) | 72.6 (69.5–75.6) | 75.1 (72.2–78.0) | 90.1 (88.8–91.4) | 82.5 | 38.1 | 10.6 | 25.4 | 2.0 | 5,431 | 675 | $0.0019 | $0.0026 |
| adv-std-hybrid-expand-rerank-s2b | 72.5 (69.3–75.5) | 66.4 (63.1–69.5) | 14 (1.7%) | 71.7 (68.6–74.8) | 73.3 (70.4–76.2) | 89.7 (88.4–91.0) | 100.0 | 26.3 | 6.5 | 35.6 | 2.0 | 36,125 | 619 | $0.0080 | $0.0110 |
| adv-std-hybrid-decompose-rerank-s2b | 69.9 (66.7–73.1) | 63.0 (59.6–66.3) | 12 (1.5%) | 68.6 (65.4–71.7) | 71.1 (68.1–74.2) | 88.8 (87.5–90.2) | 100.0 | 26.0 | 4.8 | 24.3 | 2.0 | 36,718 | 608 | $0.0081 | $0.0115 |
| adv-dense-expand | 69.1 (65.8–72.2) | 61.3 (57.9–64.6) | 16 (1.9%) | 68.0 (64.7–71.1) | 65.5 (62.3–68.8) | 85.5 (84.0–87.0) | 91.6 | 37.3 | 9.2 | 25.4 | 2.0 | 3,933 | 680 | $0.0016 | $0.0023 |
| adv-std-hybrid-expand-rerank | 68.8 (65.5–72.0) | 62.3 (59.0–65.5) | 11 (1.3%) | 67.4 (64.1–70.5) | 73.4 (70.4–76.3) | 90.0 (88.8–91.3) | 100.0 | 35.1 | 8.2 | 43.1 | 2.0 | 19,246 | 668 | $0.0046 | $0.0068 |
| closed_book | 64.9 (61.7–68.1) | – | – | 64.1 (60.8–67.4) | 0.0 (0.0–0.0) | 0.0 (0.0–0.0) | – | – | – | 95.0 | 1.0 | 80 | 2,507 | $0.0030 | $0.0047 |
| adv-hybrid-expand-rerank | 64.7 (61.4–68.0) | 56.8 (53.4–60.2) | 13 (1.6%) | 63.6 (60.3–66.9) | 74.2 (71.1–77.1) | 90.3 (89.1–91.5) | 78.8 | 38.2 | 9.0 | 108.8 | 2.0 | 5,744 | 686 | $0.0020 | $0.0030 |
| adv-hybrid-decompose-rerank-s2b | 63.7 (60.3–67.0) | 56.4 (53.0–59.8) | 10 (1.2%) | 62.6 (59.2–65.9) | 66.7 (63.6–69.9) | 86.7 (85.3–88.1) | 91.7 | 26.2 | 4.1 | 32.4 | 2.0 | 26,198 | 651 | $0.0060 | $0.0094 |
| adv-std-hybrid | 60.2 (56.8–63.6) | 54.6 (51.2–58.1) | 5 (0.6%) | 59.0 (55.6–62.4) | 64.9 (61.8–68.2) | 85.6 (84.1–87.0) | 100.0 | 34.7 | 7.0 | 14.8 | 1.0 | 19,560 | 501 | $0.0045 | $0.0075 |
| adv-hybrid-decompose | 57.8 (54.4–61.2) | 51.6 (48.1–55.0) | 11 (1.3%) | 56.3 (52.9–59.7) | 69.1 (66.0–72.2) | 87.5 (86.1–88.9) | 81.6 | 39.1 | 8.5 | 32.8 | 2.0 | 6,230 | 718 | $0.0021 | $0.0036 |
| naive-std | 57.6 (54.2–61.0) | 50.1 (46.7–53.5) | 15 (1.8%) | 56.2 (52.8–59.6) | 55.0 (51.7–58.4) | 79.5 (77.8–81.3) | 100.0 | 35.2 | 7.1 | 17.3 | 1.0 | 19,116 | 532 | $0.0045 | $0.0077 |
| adv-hybrid-decompose-rerank | 55.1 (51.7–58.5) | 47.8 (44.4–51.2) | 15 (1.8%) | 54.4 (51.0–57.8) | 67.4 (64.2–70.5) | 87.2 (85.8–88.5) | 79.0 | 37.0 | 6.6 | 144.8 | 2.0 | 6,072 | 682 | $0.0020 | $0.0037 |
| adv-dense-decompose | 52.7 (49.3–56.2) | 46.0 (42.5–49.4) | 18 (2.2%) | 51.3 (47.9–54.9) | 59.2 (55.9–62.6) | 82.1 (80.5–83.7) | 89.7 | 36.4 | 5.7 | 34.8 | 2.0 | 4,518 | 801 | $0.0019 | $0.0035 |
| adv-hybrid-rerank | 46.2 (42.8–49.6) | 39.7 (36.4–43.1) | 18 (2.2%) | 45.9 (42.5–49.3) | 56.8 (53.4–60.2) | 81.2 (79.5–82.8) | 77.1 | 35.5 | 3.9 | 20.6 | 1.0 | 6,249 | 517 | $0.0019 | $0.0040 |
| adv-hybrid | 43.3 (39.9–46.7) | 36.8 (33.5–40.0) | 9 (1.1%) | 42.1 (38.7–45.5) | 52.5 (49.3–55.9) | 79.0 (77.3–80.7) | 78.4 | 34.7 | 4.6 | 18.4 | 1.0 | 5,390 | 562 | $0.0018 | $0.0040 |
| adv-sparse | 37.9 (34.6–41.3) | 31.1 (27.9–34.3) | 13 (1.6%) | 36.7 (33.4–40.0) | 47.3 (43.9–50.7) | 75.3 (73.5–77.1) | 72.5 | 33.9 | 3.9 | 13.8 | 1.0 | 7,001 | 499 | $0.0020 | $0.0053 |
| naive_rag | 36.3 (33.0–39.6) | 27.7 (24.8–30.7) | 18 (2.2%) | 35.0 (31.8–38.2) | 36.3 (33.0–39.7) | 67.6 (65.7–69.7) | 87.5 | 34.8 | 3.3 | 17.9 | 1.0 | 3,666 | 576 | $0.0014 | $0.0039 |

### By split (95% CI)

| Split | Metric | adv-dense-decompose | adv-dense-expand | adv-hybrid | adv-hybrid-decompose | adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand | adv-hybrid-expand-rerank | adv-hybrid-expand-rerank-bge | adv-hybrid-expand-s2b | adv-hybrid-rerank | adv-sparse | adv-std-hybrid | adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | closed_book | naive-std | naive_rag | oracle | pipeshub |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dev | FRAMES acc % | 54.5 (47.5–61.5) | 70.5 (64.0–76.5) | 45.0 (38.0–52.0) | 62.5 (56.0–69.0) | 56.0 (49.0–63.0) | 69.0 (62.5–75.5) | 74.5 (68.5–80.5) | 69.0 (62.5–75.5) | 76.5 (70.5–82.5) | 79.5 (74.0–85.0) | 46.5 (40.0–53.5) | 43.5 (37.0–50.5) | 66.0 (59.5–72.5) | 75.5 (69.5–81.0) | 75.0 (69.0–81.0) | 77.0 (71.0–82.5) | 69.0 (62.5–75.0) | 60.5 (53.5–67.0) | 37.5 (31.0–44.0) | 92.5 (88.5–96.0) | 94.0 (90.5–97.0) |
| dev | Grounded acc % | 49.0 (42.0–56.0) | 63.5 (57.0–70.0) | 37.0 (30.5–44.0) | 56.5 (50.0–63.5) | 50.5 (43.5–57.5) | 60.0 (53.0–67.0) | 68.5 (62.0–75.0) | 61.0 (54.5–67.5) | 71.5 (65.5–77.5) | 73.0 (67.0–79.0) | 40.0 (33.5–47.0) | 36.0 (29.5–42.5) | 58.5 (51.5–65.0) | 69.0 (62.5–75.5) | 70.5 (64.0–76.5) | 73.5 (67.5–79.5) | – | 53.0 (46.0–60.0) | 28.5 (22.5–35.0) | 90.0 (86.0–94.0) | 82.5 (77.0–87.5) |
| dev | Memory-suspect % | 2.0 (0.5–4.0) | 2.0 (0.5–4.0) | 1.5 (0.0–3.5) | 1.0 (0.0–2.5) | 0.5 (0.0–1.5) | 1.0 (0.0–2.5) | 0.5 (0.0–1.5) | 1.5 (0.0–3.5) | 0.5 (0.0–1.5) | 0.5 (0.0–1.5) | 1.5 (0.0–3.5) | 1.0 (0.0–2.5) | 0.5 (0.0–1.5) | 1.5 (0.0–3.5) | 1.0 (0.0–2.5) | 0.5 (0.0–1.5) | – | 2.0 (0.5–4.0) | 2.5 (0.5–5.0) | 0.5 (0.0–1.5) | 1.5 (0.0–3.5) |
| heldout | FRAMES acc % | 52.1 (48.1–56.1) | 68.6 (64.9–72.3) | 42.8 (38.9–46.6) | 56.2 (52.4–60.1) | 54.8 (50.8–58.7) | 62.0 (58.2–65.7) | 73.1 (69.6–76.6) | 63.3 (59.6–67.0) | 75.2 (71.8–78.5) | 78.7 (75.5–81.7) | 46.2 (42.3–50.0) | 36.1 (32.4–39.7) | 58.3 (54.5–62.2) | 68.1 (64.4–71.8) | 66.8 (63.1–70.4) | 71.0 (67.5–74.5) | 63.6 (59.9–67.5) | 56.7 (52.9–60.6) | 35.9 (32.2–39.6) | 93.1 (91.0–95.0) | 92.3 (90.1–94.2) |
| heldout | Grounded acc % | 45.0 (41.2–49.0) | 60.6 (56.7–64.4) | 36.7 (32.9–40.2) | 50.0 (46.2–53.8) | 47.0 (42.9–50.8) | 55.3 (51.4–59.1) | 67.1 (63.5–70.8) | 55.4 (51.6–59.3) | 69.1 (65.4–72.6) | 73.2 (69.9–76.6) | 39.6 (35.7–43.4) | 29.5 (26.0–33.0) | 53.4 (49.4–57.4) | 61.1 (57.2–64.9) | 59.6 (55.9–63.3) | 64.1 (60.4–67.8) | – | 49.2 (45.2–53.0) | 27.4 (23.9–30.8) | 90.1 (87.7–92.3) | 84.5 (81.4–87.3) |
| heldout | Memory-suspect % | 2.2 (1.1–3.5) | 1.9 (1.0–3.0) | 1.0 (0.3–1.8) | 1.4 (0.6–2.4) | 2.2 (1.1–3.5) | 1.3 (0.5–2.2) | 1.0 (0.3–1.8) | 1.6 (0.6–2.7) | 0.8 (0.2–1.6) | 0.6 (0.2–1.3) | 2.4 (1.3–3.7) | 1.8 (0.8–2.9) | 0.6 (0.2–1.3) | 1.4 (0.6–2.4) | 1.4 (0.6–2.4) | 2.1 (1.0–3.4) | – | 1.8 (0.8–2.9) | 2.1 (1.0–3.4) | 0.2 (0.0–0.5) | 1.3 (0.5–2.2) |

### Accuracy by reasoning type

| Label | adv-dense-decompose | adv-dense-expand | adv-hybrid | adv-hybrid-decompose | adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand | adv-hybrid-expand-rerank | adv-hybrid-expand-rerank-bge | adv-hybrid-expand-s2b | adv-hybrid-rerank | adv-sparse | adv-std-hybrid | adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | closed_book | naive-std | naive_rag | oracle | pipeshub |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Multiple constraints | 49.7 | 69.4 | 42.1 | 56.5 | 53.9 | 61.6 | 72.5 | 63.9 | 75.0 | 77.8 | 44.6 | 38.8 | 57.6 | 68.3 | 67.8 | 71.2 | 64.3 | 55.9 | 34.2 | 92.9 | 93.4 |
| Numerical reasoning | 50.2 | 61.4 | 37.5 | 52.9 | 48.1 | 60.8 | 67.2 | 57.7 | 69.3 | 72.7 | 41.0 | 30.4 | 53.2 | 62.8 | 62.8 | 65.2 | 57.0 | 53.9 | 33.4 | 88.4 | 88.4 |
| Post processing | 48.6 | 63.6 | 40.2 | 54.2 | 52.3 | 62.6 | 68.2 | 60.7 | 72.9 | 76.6 | 47.7 | 34.6 | 57.0 | 64.5 | 61.7 | 63.6 | 55.1 | 51.4 | 38.3 | 90.7 | 83.2 |
| Tabular reasoning | 44.1 | 61.9 | 36.4 | 51.7 | 47.0 | 58.9 | 67.8 | 59.7 | 69.1 | 75.4 | 39.4 | 32.6 | 47.9 | 63.6 | 59.3 | 67.8 | 58.5 | 49.6 | 32.6 | 90.7 | 92.4 |
| Temporal reasoning | 45.0 | 65.1 | 36.0 | 50.4 | 48.9 | 58.3 | 70.9 | 58.3 | 74.8 | 75.2 | 40.3 | 30.6 | 56.8 | 66.9 | 65.1 | 67.6 | 60.4 | 54.7 | 28.8 | 91.4 | 90.6 |

### Accuracy by gold-article count

| Label | adv-dense-decompose | adv-dense-expand | adv-hybrid | adv-hybrid-decompose | adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand | adv-hybrid-expand-rerank | adv-hybrid-expand-rerank-bge | adv-hybrid-expand-s2b | adv-hybrid-rerank | adv-sparse | adv-std-hybrid | adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | closed_book | naive-std | naive_rag | oracle | pipeshub |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | 65.8 | 79.2 | 53.0 | 67.7 | 65.2 | 75.4 | 83.1 | 73.5 | 82.1 | 86.6 | 56.2 | 46.0 | 69.3 | 79.2 | 74.8 | 81.2 | 66.8 | 69.6 | 46.6 | 93.9 | 93.3 |
| 3 | 47.4 | 66.3 | 36.8 | 55.1 | 49.8 | 58.2 | 69.8 | 60.0 | 74.0 | 78.2 | 38.6 | 31.9 | 56.8 | 68.1 | 68.1 | 70.9 | 63.2 | 51.9 | 30.2 | 93.3 | 90.9 |
| 4 | 45.5 | 66.4 | 43.3 | 50.7 | 51.5 | 58.2 | 68.7 | 58.2 | 72.4 | 73.9 | 46.3 | 36.6 | 56.0 | 63.4 | 65.7 | 65.7 | 61.2 | 53.0 | 35.8 | 91.8 | 97.0 |
| 5+ | 34.8 | 46.7 | 30.4 | 42.4 | 42.4 | 48.9 | 58.7 | 58.7 | 62.0 | 62.0 | 35.9 | 30.4 | 45.7 | 53.3 | 55.4 | 57.6 | 69.6 | 41.3 | 20.7 | 90.2 | 90.2 |

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
| adv-dense-decompose | 379 | 37 | 18 | 0 | 0 | 100.0 |
| adv-dense-expand | 505 | 47 | 16 | 0 | 1 | 100.0 |
| adv-hybrid | 303 | 45 | 9 | 0 | 0 | 100.0 |
| adv-hybrid-decompose | 425 | 40 | 11 | 0 | 0 | 100.0 |
| adv-hybrid-decompose-rerank | 394 | 44 | 15 | 0 | 1 | 100.0 |
| adv-hybrid-decompose-rerank-s2b | 465 | 49 | 10 | 0 | 1 | 100.0 |
| adv-hybrid-expand | 556 | 42 | 7 | 0 | 0 | 100.0 |
| adv-hybrid-expand-rerank | 468 | 52 | 13 | 0 | 0 | 100.0 |
| adv-hybrid-expand-rerank-bge | 574 | 39 | 6 | 0 | 3 | 100.0 |
| adv-hybrid-expand-s2b | 603 | 37 | 5 | 0 | 5 | 100.0 |
| adv-hybrid-rerank | 327 | 36 | 18 | 0 | 0 | 100.0 |
| adv-sparse | 256 | 41 | 13 | 0 | 2 | 100.0 |
| adv-std-hybrid | 450 | 39 | 5 | 0 | 2 | 100.0 |
| adv-std-hybrid-decompose-rerank-s2b | 519 | 40 | 12 | 0 | 5 | 100.0 |
| adv-std-hybrid-expand-rerank | 513 | 43 | 11 | 0 | 0 | 100.0 |
| adv-std-hybrid-expand-rerank-s2b | 547 | 35 | 14 | 0 | 1 | 100.0 |
| naive-std | 413 | 45 | 15 | 0 | 2 | 100.0 |
| naive_rag | 228 | 53 | 18 | 0 | 0 | 100.0 |
| oracle | 742 | 19 | 2 | 0 | 3 | 100.0 |
| pipeshub | 692 | 59 | 11 | 0 | 2 | 100.0 |

Memory-suspect questions:

- **adv-dense-decompose**: 68, 71, 86, 158, 185, 230, 239, 310, 388, 415, 450, 540, 547, 618, 660, 688, 699, 708
- **adv-dense-expand**: 11, 43, 68, 82, 239, 240, 264, 310, 453, 645, 691, 702, 720, 728, 754, 803
- **adv-hybrid**: 37, 256, 296, 331, 410, 415, 688, 691, 699
- **adv-hybrid-decompose**: 27, 106, 172, 229, 239, 277, 383, 408, 447, 478, 728
- **adv-hybrid-decompose-rerank**: 7, 68, 142, 229, 289, 310, 325, 341, 384, 540, 585, 688, 699, 728, 744
- **adv-hybrid-decompose-rerank-s2b**: 106, 172, 256, 310, 358, 401, 415, 688, 728, 748
- **adv-hybrid-expand**: 86, 106, 229, 419, 444, 645, 728
- **adv-hybrid-expand-rerank**: 82, 161, 172, 229, 277, 282, 382, 401, 478, 596, 688, 699, 701
- **adv-hybrid-expand-rerank-bge**: 259, 285, 388, 408, 469, 618
- **adv-hybrid-expand-s2b**: 27, 229, 310, 410, 699
- **adv-hybrid-rerank**: 68, 115, 145, 172, 209, 285, 292, 302, 325, 388, 444, 621, 691, 699, 701, 705, 728, 759
- **adv-sparse**: 88, 106, 115, 194, 229, 282, 336, 447, 540, 621, 688, 691, 701
- **adv-std-hybrid**: 9, 27, 51, 136, 388
- **adv-std-hybrid-decompose-rerank-s2b**: 3, 9, 51, 82, 161, 342, 358, 388, 401, 539, 596, 701
- **adv-std-hybrid-expand-rerank**: 9, 27, 76, 92, 340, 520, 540, 679, 702, 777, 821
- **adv-std-hybrid-expand-rerank-s2b**: 27, 76, 88, 142, 149, 215, 310, 358, 478, 520, 539, 645, 688, 821
- **naive-std**: 7, 27, 37, 68, 71, 161, 172, 256, 291, 438, 446, 530, 720, 728, 748
- **naive_rag**: 11, 37, 53, 68, 169, 359, 410, 443, 473, 530, 547, 688, 690, 691, 701, 702, 728, 769
- **oracle**: 27, 652
- **pipeshub**: 68, 190, 231, 256, 343, 344, 501, 536, 652, 756, 815

### Paired comparisons (exact McNemar)

| A | B | A only | B only | p |
|---|---|---|---|---|
| adv-dense-decompose | adv-dense-expand | 45 | 180 | 0.0000 |
| adv-dense-decompose | adv-hybrid | 143 | 66 | 0.0000 |
| adv-dense-decompose | adv-hybrid-decompose | 61 | 103 | 0.0013 |
| adv-dense-decompose | adv-hybrid-decompose-rerank | 81 | 101 | 0.1588 |
| adv-dense-decompose | adv-hybrid-decompose-rerank-s2b | 55 | 146 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand | 35 | 206 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-rerank | 70 | 169 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-rerank-bge | 32 | 220 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-s2b | 23 | 239 | 0.0000 |
| adv-dense-decompose | adv-hybrid-rerank | 137 | 84 | 0.0004 |
| adv-dense-decompose | adv-sparse | 195 | 73 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid | 87 | 149 | 0.0001 |
| adv-dense-decompose | adv-std-hybrid-decompose-rerank-s2b | 50 | 192 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid-expand-rerank | 61 | 194 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid-expand-rerank-s2b | 50 | 213 | 0.0000 |
| adv-dense-decompose | closed_book | 111 | 212 | 0.0000 |
| adv-dense-decompose | naive-std | 91 | 132 | 0.0073 |
| adv-dense-decompose | naive_rag | 168 | 33 | 0.0000 |
| adv-dense-decompose | oracle | 11 | 343 | 0.0000 |
| adv-dense-decompose | pipeshub | 5 | 335 | 0.0000 |
| adv-dense-expand | adv-hybrid | 252 | 40 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose | 161 | 68 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose-rerank | 176 | 61 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose-rerank-s2b | 139 | 95 | 0.0048 |
| adv-dense-expand | adv-hybrid-expand | 49 | 85 | 0.0024 |
| adv-dense-expand | adv-hybrid-expand-rerank | 118 | 82 | 0.0131 |
| adv-dense-expand | adv-hybrid-expand-rerank-bge | 48 | 101 | 0.0000 |
| adv-dense-expand | adv-hybrid-expand-s2b | 35 | 116 | 0.0000 |
| adv-dense-expand | adv-hybrid-rerank | 240 | 52 | 0.0000 |
| adv-dense-expand | adv-sparse | 303 | 46 | 0.0000 |
| adv-dense-expand | adv-std-hybrid | 166 | 93 | 0.0000 |
| adv-dense-expand | adv-std-hybrid-decompose-rerank-s2b | 116 | 123 | 0.6980 |
| adv-dense-expand | adv-std-hybrid-expand-rerank | 116 | 114 | 0.9474 |
| adv-dense-expand | adv-std-hybrid-expand-rerank-s2b | 103 | 131 | 0.0773 |
| adv-dense-expand | closed_book | 136 | 102 | 0.0322 |
| adv-dense-expand | naive-std | 170 | 76 | 0.0000 |
| adv-dense-expand | naive_rag | 288 | 18 | 0.0000 |
| adv-dense-expand | oracle | 10 | 207 | 0.0000 |
| adv-dense-expand | pipeshub | 11 | 206 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose | 26 | 145 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose-rerank | 38 | 135 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose-rerank-s2b | 25 | 193 | 0.0000 |
| adv-hybrid | adv-hybrid-expand | 12 | 260 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-rerank | 41 | 217 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-rerank-bge | 16 | 281 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-s2b | 9 | 302 | 0.0000 |
| adv-hybrid | adv-hybrid-rerank | 39 | 63 | 0.0223 |
| adv-hybrid | adv-sparse | 80 | 35 | 0.0000 |
| adv-hybrid | adv-std-hybrid | 33 | 172 | 0.0000 |
| adv-hybrid | adv-std-hybrid-decompose-rerank-s2b | 24 | 243 | 0.0000 |
| adv-hybrid | adv-std-hybrid-expand-rerank | 33 | 243 | 0.0000 |
| adv-hybrid | adv-std-hybrid-expand-rerank-s2b | 23 | 263 | 0.0000 |
| adv-hybrid | closed_book | 96 | 274 | 0.0000 |
| adv-hybrid | naive-std | 50 | 168 | 0.0000 |
| adv-hybrid | naive_rag | 99 | 41 | 0.0000 |
| adv-hybrid | oracle | 4 | 413 | 0.0000 |
| adv-hybrid | pipeshub | 4 | 411 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-decompose-rerank | 84 | 62 | 0.0819 |
| adv-hybrid-decompose | adv-hybrid-decompose-rerank-s2b | 58 | 107 | 0.0002 |
| adv-hybrid-decompose | adv-hybrid-expand | 38 | 167 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-expand-rerank | 77 | 134 | 0.0001 |
| adv-hybrid-decompose | adv-hybrid-expand-rerank-bge | 31 | 177 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-expand-s2b | 28 | 202 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-rerank | 135 | 40 | 0.0000 |
| adv-hybrid-decompose | adv-sparse | 193 | 29 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid | 86 | 106 | 0.1701 |
| adv-hybrid-decompose | adv-std-hybrid-decompose-rerank-s2b | 50 | 150 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid-expand-rerank | 64 | 155 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid-expand-rerank-s2b | 48 | 169 | 0.0000 |
| adv-hybrid-decompose | closed_book | 135 | 194 | 0.0013 |
| adv-hybrid-decompose | naive-std | 109 | 108 | 1.0000 |
| adv-hybrid-decompose | naive_rag | 203 | 26 | 0.0000 |
| adv-hybrid-decompose | oracle | 9 | 299 | 0.0000 |
| adv-hybrid-decompose | pipeshub | 8 | 296 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | 32 | 103 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand | 43 | 194 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-rerank | 40 | 119 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-rerank-bge | 31 | 199 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-s2b | 29 | 225 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-rerank | 106 | 33 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-sparse | 170 | 28 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid | 71 | 113 | 0.0024 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-decompose-rerank-s2b | 42 | 164 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-expand-rerank | 42 | 155 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-expand-rerank-s2b | 39 | 182 | 0.0000 |
| adv-hybrid-decompose-rerank | closed_book | 127 | 208 | 0.0000 |
| adv-hybrid-decompose-rerank | naive-std | 92 | 113 | 0.1623 |
| adv-hybrid-decompose-rerank | naive_rag | 193 | 38 | 0.0000 |
| adv-hybrid-decompose-rerank | oracle | 5 | 317 | 0.0000 |
| adv-hybrid-decompose-rerank | pipeshub | 8 | 318 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand | 69 | 149 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank | 86 | 94 | 0.6020 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank-bge | 55 | 152 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-s2b | 36 | 161 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-rerank | 169 | 25 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-sparse | 233 | 20 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid | 104 | 75 | 0.0361 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-decompose-rerank-s2b | 47 | 98 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 65 | 107 | 0.0017 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank-s2b | 45 | 117 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | closed_book | 149 | 159 | 0.6081 |
| adv-hybrid-decompose-rerank-s2b | naive-std | 125 | 75 | 0.0005 |
| adv-hybrid-decompose-rerank-s2b | naive_rag | 251 | 25 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | oracle | 9 | 250 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | pipeshub | 10 | 249 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-expand-rerank | 122 | 50 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-expand-rerank-bge | 50 | 67 | 0.1388 |
| adv-hybrid-expand | adv-hybrid-expand-s2b | 33 | 78 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-rerank | 250 | 26 | 0.0000 |
| adv-hybrid-expand | adv-sparse | 311 | 18 | 0.0000 |
| adv-hybrid-expand | adv-std-hybrid | 175 | 66 | 0.0000 |
| adv-hybrid-expand | adv-std-hybrid-decompose-rerank-s2b | 121 | 92 | 0.0548 |
| adv-hybrid-expand | adv-std-hybrid-expand-rerank | 125 | 87 | 0.0109 |
| adv-hybrid-expand | adv-std-hybrid-expand-rerank-s2b | 98 | 90 | 0.6098 |
| adv-hybrid-expand | closed_book | 154 | 84 | 0.0000 |
| adv-hybrid-expand | naive-std | 190 | 60 | 0.0000 |
| adv-hybrid-expand | naive_rag | 323 | 17 | 0.0000 |
| adv-hybrid-expand | oracle | 12 | 173 | 0.0000 |
| adv-hybrid-expand | pipeshub | 11 | 170 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-expand-rerank-bge | 37 | 126 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-expand-s2b | 39 | 156 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-rerank | 182 | 30 | 0.0000 |
| adv-hybrid-expand-rerank | adv-sparse | 248 | 27 | 0.0000 |
| adv-hybrid-expand-rerank | adv-std-hybrid | 127 | 90 | 0.0143 |
| adv-hybrid-expand-rerank | adv-std-hybrid-decompose-rerank-s2b | 82 | 125 | 0.0034 |
| adv-hybrid-expand-rerank | adv-std-hybrid-expand-rerank | 76 | 110 | 0.0153 |
| adv-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | 64 | 128 | 0.0000 |
| adv-hybrid-expand-rerank | closed_book | 132 | 134 | 0.9511 |
| adv-hybrid-expand-rerank | naive-std | 148 | 90 | 0.0002 |
| adv-hybrid-expand-rerank | naive_rag | 272 | 38 | 0.0000 |
| adv-hybrid-expand-rerank | oracle | 9 | 242 | 0.0000 |
| adv-hybrid-expand-rerank | pipeshub | 13 | 244 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-hybrid-expand-s2b | 40 | 68 | 0.0091 |
| adv-hybrid-expand-rerank-bge | adv-hybrid-rerank | 259 | 18 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-sparse | 321 | 11 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid | 181 | 55 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-decompose-rerank-s2b | 122 | 76 | 0.0013 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-expand-rerank | 124 | 69 | 0.0001 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-expand-rerank-s2b | 99 | 74 | 0.0677 |
| adv-hybrid-expand-rerank-bge | closed_book | 168 | 81 | 0.0000 |
| adv-hybrid-expand-rerank-bge | naive-std | 196 | 49 | 0.0000 |
| adv-hybrid-expand-rerank-bge | naive_rag | 339 | 16 | 0.0000 |
| adv-hybrid-expand-rerank-bge | oracle | 14 | 158 | 0.0000 |
| adv-hybrid-expand-rerank-bge | pipeshub | 11 | 153 | 0.0000 |
| adv-hybrid-expand-s2b | adv-hybrid-rerank | 294 | 25 | 0.0000 |
| adv-hybrid-expand-s2b | adv-sparse | 350 | 12 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid | 195 | 41 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-decompose-rerank-s2b | 132 | 58 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank | 137 | 54 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank-s2b | 105 | 52 | 0.0000 |
| adv-hybrid-expand-s2b | closed_book | 178 | 63 | 0.0000 |
| adv-hybrid-expand-s2b | naive-std | 217 | 42 | 0.0000 |
| adv-hybrid-expand-s2b | naive_rag | 369 | 18 | 0.0000 |
| adv-hybrid-expand-s2b | oracle | 12 | 128 | 0.0000 |
| adv-hybrid-expand-s2b | pipeshub | 16 | 130 | 0.0000 |
| adv-hybrid-rerank | adv-sparse | 96 | 27 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid | 42 | 157 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-decompose-rerank-s2b | 29 | 224 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-expand-rerank | 28 | 214 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-expand-rerank-s2b | 25 | 241 | 0.0000 |
| adv-hybrid-rerank | closed_book | 112 | 266 | 0.0000 |
| adv-hybrid-rerank | naive-std | 55 | 149 | 0.0000 |
| adv-hybrid-rerank | naive_rag | 117 | 35 | 0.0000 |
| adv-hybrid-rerank | oracle | 9 | 394 | 0.0000 |
| adv-hybrid-rerank | pipeshub | 8 | 391 | 0.0000 |
| adv-sparse | adv-std-hybrid | 31 | 215 | 0.0000 |
| adv-sparse | adv-std-hybrid-decompose-rerank-s2b | 21 | 285 | 0.0000 |
| adv-sparse | adv-std-hybrid-expand-rerank | 24 | 279 | 0.0000 |
| adv-sparse | adv-std-hybrid-expand-rerank-s2b | 17 | 302 | 0.0000 |
| adv-sparse | closed_book | 88 | 311 | 0.0000 |
| adv-sparse | naive-std | 49 | 212 | 0.0000 |
| adv-sparse | naive_rag | 110 | 97 | 0.4043 |
| adv-sparse | oracle | 5 | 459 | 0.0000 |
| adv-sparse | pipeshub | 3 | 455 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-decompose-rerank-s2b | 39 | 119 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-expand-rerank | 40 | 111 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-expand-rerank-s2b | 38 | 139 | 0.0000 |
| adv-std-hybrid | closed_book | 138 | 177 | 0.0321 |
| adv-std-hybrid | naive-std | 75 | 54 | 0.0778 |
| adv-std-hybrid | naive_rag | 227 | 30 | 0.0000 |
| adv-std-hybrid | oracle | 9 | 279 | 0.0000 |
| adv-std-hybrid | pipeshub | 7 | 275 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 68 | 59 | 0.4779 |
| adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank-s2b | 38 | 59 | 0.0417 |
| adv-std-hybrid-decompose-rerank-s2b | closed_book | 171 | 130 | 0.0210 |
| adv-std-hybrid-decompose-rerank-s2b | naive-std | 147 | 46 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | naive_rag | 299 | 22 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | oracle | 10 | 200 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | pipeshub | 16 | 204 | 0.0000 |
| adv-std-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | 33 | 63 | 0.0029 |
| adv-std-hybrid-expand-rerank | closed_book | 172 | 140 | 0.0791 |
| adv-std-hybrid-expand-rerank | naive-std | 138 | 46 | 0.0000 |
| adv-std-hybrid-expand-rerank | naive_rag | 298 | 30 | 0.0000 |
| adv-std-hybrid-expand-rerank | oracle | 11 | 210 | 0.0000 |
| adv-std-hybrid-expand-rerank | pipeshub | 11 | 208 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | closed_book | 177 | 115 | 0.0003 |
| adv-std-hybrid-expand-rerank-s2b | naive-std | 166 | 44 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | naive_rag | 322 | 24 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | oracle | 12 | 181 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | pipeshub | 11 | 178 | 0.0000 |
| closed_book | naive-std | 193 | 133 | 0.0011 |
| closed_book | naive_rag | 319 | 83 | 0.0000 |
| closed_book | oracle | 26 | 257 | 0.0000 |
| closed_book | pipeshub | 20 | 249 | 0.0000 |
| naive-std | naive_rag | 207 | 31 | 0.0000 |
| naive-std | oracle | 9 | 300 | 0.0000 |
| naive-std | pipeshub | 6 | 295 | 0.0000 |
| naive_rag | oracle | 6 | 473 | 0.0000 |
| naive_rag | pipeshub | 6 | 471 | 0.0000 |
| oracle | pipeshub | 29 | 27 | 0.8939 |

### Paired comparisons on grounded correctness (exact McNemar)

| A | B | A only | B only | p |
|---|---|---|---|---|
| adv-dense-decompose | adv-dense-expand | 52 | 178 | 0.0000 |
| adv-dense-decompose | adv-hybrid | 145 | 69 | 0.0000 |
| adv-dense-decompose | adv-hybrid-decompose | 53 | 99 | 0.0002 |
| adv-dense-decompose | adv-hybrid-decompose-rerank | 86 | 101 | 0.3059 |
| adv-dense-decompose | adv-hybrid-decompose-rerank-s2b | 64 | 150 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand | 36 | 213 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-rerank | 75 | 164 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-rerank-bge | 36 | 231 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-s2b | 24 | 248 | 0.0000 |
| adv-dense-decompose | adv-hybrid-rerank | 139 | 87 | 0.0007 |
| adv-dense-decompose | adv-sparse | 190 | 67 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid | 85 | 156 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid-decompose-rerank-s2b | 54 | 194 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid-expand-rerank | 66 | 200 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid-expand-rerank-s2b | 56 | 224 | 0.0000 |
| adv-dense-decompose | naive-std | 100 | 134 | 0.0308 |
| adv-dense-decompose | naive_rag | 184 | 33 | 0.0000 |
| adv-dense-decompose | oracle | 14 | 377 | 0.0000 |
| adv-dense-decompose | pipeshub | 21 | 334 | 0.0000 |
| adv-dense-expand | adv-hybrid | 248 | 46 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose | 154 | 74 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose-rerank | 180 | 69 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose-rerank-s2b | 145 | 105 | 0.0135 |
| adv-dense-expand | adv-hybrid-expand | 54 | 105 | 0.0001 |
| adv-dense-expand | adv-hybrid-expand-rerank | 121 | 84 | 0.0117 |
| adv-dense-expand | adv-hybrid-expand-rerank-bge | 51 | 120 | 0.0000 |
| adv-dense-expand | adv-hybrid-expand-s2b | 35 | 133 | 0.0000 |
| adv-dense-expand | adv-hybrid-rerank | 236 | 58 | 0.0000 |
| adv-dense-expand | adv-sparse | 293 | 44 | 0.0000 |
| adv-dense-expand | adv-std-hybrid | 161 | 106 | 0.0009 |
| adv-dense-expand | adv-std-hybrid-decompose-rerank-s2b | 114 | 128 | 0.4034 |
| adv-dense-expand | adv-std-hybrid-expand-rerank | 119 | 127 | 0.6555 |
| adv-dense-expand | adv-std-hybrid-expand-rerank-s2b | 105 | 147 | 0.0097 |
| adv-dense-expand | naive-std | 171 | 79 | 0.0000 |
| adv-dense-expand | naive_rag | 300 | 23 | 0.0000 |
| adv-dense-expand | oracle | 12 | 249 | 0.0000 |
| adv-dense-expand | pipeshub | 33 | 220 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose | 27 | 149 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose-rerank | 39 | 130 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose-rerank-s2b | 32 | 194 | 0.0000 |
| adv-hybrid | adv-hybrid-expand | 17 | 270 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-rerank | 41 | 206 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-rerank-bge | 19 | 290 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-s2b | 11 | 311 | 0.0000 |
| adv-hybrid | adv-hybrid-rerank | 39 | 63 | 0.0223 |
| adv-hybrid | adv-sparse | 84 | 37 | 0.0000 |
| adv-hybrid | adv-std-hybrid | 32 | 179 | 0.0000 |
| adv-hybrid | adv-std-hybrid-decompose-rerank-s2b | 24 | 240 | 0.0000 |
| adv-hybrid | adv-std-hybrid-expand-rerank | 34 | 244 | 0.0000 |
| adv-hybrid | adv-std-hybrid-expand-rerank-s2b | 27 | 271 | 0.0000 |
| adv-hybrid | naive-std | 56 | 166 | 0.0000 |
| adv-hybrid | naive_rag | 115 | 40 | 0.0000 |
| adv-hybrid | oracle | 5 | 444 | 0.0000 |
| adv-hybrid | pipeshub | 15 | 404 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-decompose-rerank | 89 | 58 | 0.0131 |
| adv-hybrid-decompose | adv-hybrid-decompose-rerank-s2b | 72 | 112 | 0.0039 |
| adv-hybrid-decompose | adv-hybrid-expand | 41 | 172 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-expand-rerank | 87 | 130 | 0.0042 |
| adv-hybrid-decompose | adv-hybrid-expand-rerank-bge | 36 | 185 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-expand-s2b | 29 | 207 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-rerank | 142 | 44 | 0.0000 |
| adv-hybrid-decompose | adv-sparse | 199 | 30 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid | 89 | 114 | 0.0918 |
| adv-hybrid-decompose | adv-std-hybrid-decompose-rerank-s2b | 61 | 155 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid-expand-rerank | 74 | 162 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid-expand-rerank-s2b | 59 | 181 | 0.0000 |
| adv-hybrid-decompose | naive-std | 123 | 111 | 0.4722 |
| adv-hybrid-decompose | naive_rag | 220 | 23 | 0.0000 |
| adv-hybrid-decompose | oracle | 10 | 327 | 0.0000 |
| adv-hybrid-decompose | pipeshub | 31 | 298 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | 38 | 109 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand | 41 | 203 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-rerank | 44 | 118 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-rerank-bge | 32 | 212 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-s2b | 27 | 236 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-rerank | 106 | 39 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-sparse | 171 | 33 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid | 71 | 127 | 0.0001 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-decompose-rerank-s2b | 39 | 164 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-expand-rerank | 49 | 168 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-expand-rerank-s2b | 42 | 195 | 0.0000 |
| adv-hybrid-decompose-rerank | naive-std | 100 | 119 | 0.2238 |
| adv-hybrid-decompose-rerank | naive_rag | 202 | 36 | 0.0000 |
| adv-hybrid-decompose-rerank | oracle | 6 | 354 | 0.0000 |
| adv-hybrid-decompose-rerank | pipeshub | 29 | 327 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand | 72 | 163 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank | 93 | 96 | 0.8844 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank-bge | 62 | 171 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-s2b | 36 | 174 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-rerank | 168 | 30 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-sparse | 239 | 30 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid | 106 | 91 | 0.3185 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-decompose-rerank-s2b | 52 | 106 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 69 | 117 | 0.0005 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank-s2b | 50 | 132 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | naive-std | 130 | 78 | 0.0004 |
| adv-hybrid-decompose-rerank-s2b | naive_rag | 262 | 25 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | oracle | 10 | 287 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | pipeshub | 34 | 261 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-expand-rerank | 138 | 50 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-expand-rerank-bge | 57 | 75 | 0.1387 |
| adv-hybrid-expand | adv-hybrid-expand-s2b | 39 | 86 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-rerank | 257 | 28 | 0.0000 |
| adv-hybrid-expand | adv-sparse | 319 | 19 | 0.0000 |
| adv-hybrid-expand | adv-std-hybrid | 176 | 70 | 0.0000 |
| adv-hybrid-expand | adv-std-hybrid-decompose-rerank-s2b | 132 | 95 | 0.0167 |
| adv-hybrid-expand | adv-std-hybrid-expand-rerank | 139 | 96 | 0.0060 |
| adv-hybrid-expand | adv-std-hybrid-expand-rerank-s2b | 116 | 107 | 0.5923 |
| adv-hybrid-expand | naive-std | 207 | 64 | 0.0000 |
| adv-hybrid-expand | naive_rag | 346 | 18 | 0.0000 |
| adv-hybrid-expand | oracle | 15 | 201 | 0.0000 |
| adv-hybrid-expand | pipeshub | 36 | 172 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-expand-rerank-bge | 32 | 138 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-expand-s2b | 37 | 172 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-rerank | 173 | 32 | 0.0000 |
| adv-hybrid-expand-rerank | adv-sparse | 243 | 31 | 0.0000 |
| adv-hybrid-expand-rerank | adv-std-hybrid | 121 | 103 | 0.2560 |
| adv-hybrid-expand-rerank | adv-std-hybrid-decompose-rerank-s2b | 84 | 135 | 0.0007 |
| adv-hybrid-expand-rerank | adv-std-hybrid-expand-rerank | 80 | 125 | 0.0020 |
| adv-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | 70 | 149 | 0.0000 |
| adv-hybrid-expand-rerank | naive-std | 150 | 95 | 0.0005 |
| adv-hybrid-expand-rerank | naive_rag | 274 | 34 | 0.0000 |
| adv-hybrid-expand-rerank | oracle | 7 | 281 | 0.0000 |
| adv-hybrid-expand-rerank | pipeshub | 33 | 257 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-hybrid-expand-s2b | 53 | 82 | 0.0156 |
| adv-hybrid-expand-rerank-bge | adv-hybrid-rerank | 268 | 21 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-sparse | 332 | 14 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid | 186 | 62 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-decompose-rerank-s2b | 138 | 83 | 0.0003 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-expand-rerank | 144 | 83 | 0.0001 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-expand-rerank-s2b | 118 | 91 | 0.0719 |
| adv-hybrid-expand-rerank-bge | naive-std | 218 | 57 | 0.0000 |
| adv-hybrid-expand-rerank-bge | naive_rag | 367 | 21 | 0.0000 |
| adv-hybrid-expand-rerank-bge | oracle | 20 | 188 | 0.0000 |
| adv-hybrid-expand-rerank-bge | pipeshub | 39 | 157 | 0.0000 |
| adv-hybrid-expand-s2b | adv-hybrid-rerank | 297 | 21 | 0.0000 |
| adv-hybrid-expand-s2b | adv-sparse | 360 | 13 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid | 197 | 44 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-decompose-rerank-s2b | 142 | 58 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank | 152 | 62 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank-s2b | 122 | 66 | 0.0001 |
| adv-hybrid-expand-s2b | naive-std | 230 | 40 | 0.0000 |
| adv-hybrid-expand-s2b | naive_rag | 390 | 15 | 0.0000 |
| adv-hybrid-expand-s2b | oracle | 17 | 156 | 0.0000 |
| adv-hybrid-expand-s2b | pipeshub | 45 | 134 | 0.0000 |
| adv-hybrid-rerank | adv-sparse | 98 | 27 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid | 39 | 162 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-decompose-rerank-s2b | 27 | 219 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-expand-rerank | 31 | 217 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-expand-rerank-s2b | 27 | 247 | 0.0000 |
| adv-hybrid-rerank | naive-std | 62 | 148 | 0.0000 |
| adv-hybrid-rerank | naive_rag | 135 | 36 | 0.0000 |
| adv-hybrid-rerank | oracle | 7 | 422 | 0.0000 |
| adv-hybrid-rerank | pipeshub | 23 | 388 | 0.0000 |
| adv-sparse | adv-std-hybrid | 28 | 222 | 0.0000 |
| adv-sparse | adv-std-hybrid-decompose-rerank-s2b | 20 | 283 | 0.0000 |
| adv-sparse | adv-std-hybrid-expand-rerank | 23 | 280 | 0.0000 |
| adv-sparse | adv-std-hybrid-expand-rerank-s2b | 20 | 311 | 0.0000 |
| adv-sparse | naive-std | 48 | 205 | 0.0000 |
| adv-sparse | naive_rag | 110 | 82 | 0.0511 |
| adv-sparse | oracle | 4 | 490 | 0.0000 |
| adv-sparse | pipeshub | 12 | 448 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-decompose-rerank-s2b | 45 | 114 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-expand-rerank | 48 | 111 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-expand-rerank-s2b | 42 | 139 | 0.0000 |
| adv-std-hybrid | naive-std | 91 | 54 | 0.0027 |
| adv-std-hybrid | naive_rag | 251 | 29 | 0.0000 |
| adv-std-hybrid | oracle | 11 | 303 | 0.0000 |
| adv-std-hybrid | pipeshub | 31 | 273 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 69 | 63 | 0.6636 |
| adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank-s2b | 42 | 70 | 0.0104 |
| adv-std-hybrid-decompose-rerank-s2b | naive-std | 153 | 47 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | naive_rag | 309 | 18 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | oracle | 13 | 236 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | pipeshub | 46 | 219 | 0.0000 |
| adv-std-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | 38 | 72 | 0.0015 |
| adv-std-hybrid-expand-rerank | naive-std | 149 | 49 | 0.0000 |
| adv-std-hybrid-expand-rerank | naive_rag | 313 | 28 | 0.0000 |
| adv-std-hybrid-expand-rerank | oracle | 15 | 244 | 0.0000 |
| adv-std-hybrid-expand-rerank | pipeshub | 41 | 220 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | naive-std | 179 | 45 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | naive_rag | 345 | 26 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | oracle | 18 | 213 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | pipeshub | 42 | 187 | 0.0000 |
| naive-std | naive_rag | 212 | 27 | 0.0000 |
| naive-std | oracle | 11 | 340 | 0.0000 |
| naive-std | pipeshub | 30 | 309 | 0.0000 |
| naive_rag | oracle | 5 | 519 | 0.0000 |
| naive_rag | pipeshub | 13 | 477 | 0.0000 |
| oracle | pipeshub | 79 | 29 | 0.0000 |

Judge agreement (Cohen's κ, primary vs secondary): adv-dense-decompose: 0.971, adv-dense-expand: 0.958, adv-hybrid: 0.955, adv-hybrid-decompose: 0.965, adv-hybrid-decompose-rerank: 0.971, adv-hybrid-decompose-rerank-s2b: 0.979, adv-hybrid-expand: 0.957, adv-hybrid-expand-rerank: 0.979, adv-hybrid-expand-rerank-bge: 0.949, adv-hybrid-expand-s2b: 0.975, adv-hybrid-rerank: 0.976, adv-sparse: 0.958, adv-std-hybrid: 0.962, adv-std-hybrid-decompose-rerank-s2b: 0.974, adv-std-hybrid-expand-rerank: 0.958, adv-std-hybrid-expand-rerank-s2b: 0.970, closed_book: 0.984, naive-std: 0.965, naive_rag: 0.958, oracle: 0.930, pipeshub: 0.982


# View: questions no system was refused on (0 excluded)

## FRAMES benchmark — `questions no system was refused on (0 excluded)`

Status: VALID

### Board

| System | FRAMES acc % (95% CI) | Grounded acc % (95% CI) | Memory-suspect | Strict % | All gold in context % | Context recall % | Citation integrity % | ALCE recall % | Correct ∧ grounded % | p95 latency s | LLM calls / q | Input tok / q | Output tok / q | Cost / q | Cost / correct |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| oracle | 93.0 (91.1–94.7) | 90.0 (88.0–92.0) | 2 (0.2%) | 91.9 (89.9–93.7) | 99.8 (99.4–100.0) | 99.9 (99.8–100.0) | – | – | – | 6.3 | 1.0 | 23,167 | 234 | $0.0049 | $0.0053 |
| pipeshub | 92.7 (90.9–94.4) | 84.0 (81.4–86.4) | 11 (1.3%) | 91.6 (89.7–93.4) | 83.6 (81.1–86.0) | 94.4 (93.5–95.3) | 98.5 | 42.5 | 17.7 | 133.5 | 2.6 | 137,076 | 824 | $0.0167 | $0.0180 |
| adv-hybrid-expand-s2b | 78.9 (76.1–81.6) | 73.2 (70.1–76.2) | 5 (0.6%) | 78.0 (75.1–80.8) | 75.4 (72.5–78.3) | 90.4 (89.2–91.6) | 92.5 | 26.6 | 6.0 | 54.2 | 2.0 | 24,674 | 568 | $0.0056 | $0.0071 |
| adv-hybrid-expand-rerank-bge | 75.5 (72.6–78.3) | 69.7 (66.5–72.7) | 6 (0.7%) | 74.2 (71.1–77.1) | 78.9 (76.1–81.6) | 92.2 (91.1–93.3) | 78.6 | 39.5 | 10.2 | 328.5 | 2.0 | 6,932 | 673 | $0.0022 | $0.0029 |
| adv-hybrid-expand | 73.4 (70.4–76.5) | 67.5 (64.3–70.8) | 7 (0.8%) | 72.6 (69.5–75.6) | 75.1 (72.2–78.0) | 90.1 (88.8–91.4) | 82.5 | 38.1 | 10.6 | 25.4 | 2.0 | 5,431 | 675 | $0.0019 | $0.0026 |
| adv-std-hybrid-expand-rerank-s2b | 72.5 (69.3–75.5) | 66.4 (63.1–69.5) | 14 (1.7%) | 71.7 (68.6–74.8) | 73.3 (70.4–76.2) | 89.7 (88.4–91.0) | 100.0 | 26.3 | 6.5 | 35.6 | 2.0 | 36,125 | 619 | $0.0080 | $0.0110 |
| adv-std-hybrid-decompose-rerank-s2b | 69.9 (66.7–73.1) | 63.0 (59.6–66.3) | 12 (1.5%) | 68.6 (65.4–71.7) | 71.1 (68.1–74.2) | 88.8 (87.5–90.2) | 100.0 | 26.0 | 4.8 | 24.3 | 2.0 | 36,718 | 608 | $0.0081 | $0.0115 |
| adv-dense-expand | 69.1 (65.8–72.2) | 61.3 (57.9–64.6) | 16 (1.9%) | 68.0 (64.7–71.1) | 65.5 (62.3–68.8) | 85.5 (84.0–87.0) | 91.6 | 37.3 | 9.2 | 25.4 | 2.0 | 3,933 | 680 | $0.0016 | $0.0023 |
| adv-std-hybrid-expand-rerank | 68.8 (65.5–72.0) | 62.3 (59.0–65.5) | 11 (1.3%) | 67.4 (64.1–70.5) | 73.4 (70.4–76.3) | 90.0 (88.8–91.3) | 100.0 | 35.1 | 8.2 | 43.1 | 2.0 | 19,246 | 668 | $0.0046 | $0.0068 |
| closed_book | 64.9 (61.7–68.1) | – | – | 64.1 (60.8–67.4) | 0.0 (0.0–0.0) | 0.0 (0.0–0.0) | – | – | – | 95.0 | 1.0 | 80 | 2,507 | $0.0030 | $0.0047 |
| adv-hybrid-expand-rerank | 64.7 (61.4–68.0) | 56.8 (53.4–60.2) | 13 (1.6%) | 63.6 (60.3–66.9) | 74.2 (71.1–77.1) | 90.3 (89.1–91.5) | 78.8 | 38.2 | 9.0 | 108.8 | 2.0 | 5,744 | 686 | $0.0020 | $0.0030 |
| adv-hybrid-decompose-rerank-s2b | 63.7 (60.3–67.0) | 56.4 (53.0–59.8) | 10 (1.2%) | 62.6 (59.2–65.9) | 66.7 (63.6–69.9) | 86.7 (85.3–88.1) | 91.7 | 26.2 | 4.1 | 32.4 | 2.0 | 26,198 | 651 | $0.0060 | $0.0094 |
| adv-std-hybrid | 60.2 (56.8–63.6) | 54.6 (51.2–58.1) | 5 (0.6%) | 59.0 (55.6–62.4) | 64.9 (61.8–68.2) | 85.6 (84.1–87.0) | 100.0 | 34.7 | 7.0 | 14.8 | 1.0 | 19,560 | 501 | $0.0045 | $0.0075 |
| adv-hybrid-decompose | 57.8 (54.4–61.2) | 51.6 (48.1–55.0) | 11 (1.3%) | 56.3 (52.9–59.7) | 69.1 (66.0–72.2) | 87.5 (86.1–88.9) | 81.6 | 39.1 | 8.5 | 32.8 | 2.0 | 6,230 | 718 | $0.0021 | $0.0036 |
| naive-std | 57.6 (54.2–61.0) | 50.1 (46.7–53.5) | 15 (1.8%) | 56.2 (52.8–59.6) | 55.0 (51.7–58.4) | 79.5 (77.8–81.3) | 100.0 | 35.2 | 7.1 | 17.3 | 1.0 | 19,116 | 532 | $0.0045 | $0.0077 |
| adv-hybrid-decompose-rerank | 55.1 (51.7–58.5) | 47.8 (44.4–51.2) | 15 (1.8%) | 54.4 (51.0–57.8) | 67.4 (64.2–70.5) | 87.2 (85.8–88.5) | 79.0 | 37.0 | 6.6 | 144.8 | 2.0 | 6,072 | 682 | $0.0020 | $0.0037 |
| adv-dense-decompose | 52.7 (49.3–56.2) | 46.0 (42.5–49.4) | 18 (2.2%) | 51.3 (47.9–54.9) | 59.2 (55.9–62.6) | 82.1 (80.5–83.7) | 89.7 | 36.4 | 5.7 | 34.8 | 2.0 | 4,518 | 801 | $0.0019 | $0.0035 |
| adv-hybrid-rerank | 46.2 (42.8–49.6) | 39.7 (36.4–43.1) | 18 (2.2%) | 45.9 (42.5–49.3) | 56.8 (53.4–60.2) | 81.2 (79.5–82.8) | 77.1 | 35.5 | 3.9 | 20.6 | 1.0 | 6,249 | 517 | $0.0019 | $0.0040 |
| adv-hybrid | 43.3 (39.9–46.7) | 36.8 (33.5–40.0) | 9 (1.1%) | 42.1 (38.7–45.5) | 52.5 (49.3–55.9) | 79.0 (77.3–80.7) | 78.4 | 34.7 | 4.6 | 18.4 | 1.0 | 5,390 | 562 | $0.0018 | $0.0040 |
| adv-sparse | 37.9 (34.6–41.3) | 31.1 (27.9–34.3) | 13 (1.6%) | 36.7 (33.4–40.0) | 47.3 (43.9–50.7) | 75.3 (73.5–77.1) | 72.5 | 33.9 | 3.9 | 13.8 | 1.0 | 7,001 | 499 | $0.0020 | $0.0053 |
| naive_rag | 36.3 (33.0–39.6) | 27.7 (24.8–30.7) | 18 (2.2%) | 35.0 (31.8–38.2) | 36.3 (33.0–39.7) | 67.6 (65.7–69.7) | 87.5 | 34.8 | 3.3 | 17.9 | 1.0 | 3,666 | 576 | $0.0014 | $0.0039 |

### By split (95% CI)

| Split | Metric | adv-dense-decompose | adv-dense-expand | adv-hybrid | adv-hybrid-decompose | adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand | adv-hybrid-expand-rerank | adv-hybrid-expand-rerank-bge | adv-hybrid-expand-s2b | adv-hybrid-rerank | adv-sparse | adv-std-hybrid | adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | closed_book | naive-std | naive_rag | oracle | pipeshub |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dev | FRAMES acc % | 54.5 (47.5–61.5) | 70.5 (64.0–76.5) | 45.0 (38.0–52.0) | 62.5 (56.0–69.0) | 56.0 (49.0–63.0) | 69.0 (62.5–75.5) | 74.5 (68.5–80.5) | 69.0 (62.5–75.5) | 76.5 (70.5–82.5) | 79.5 (74.0–85.0) | 46.5 (40.0–53.5) | 43.5 (37.0–50.5) | 66.0 (59.5–72.5) | 75.5 (69.5–81.0) | 75.0 (69.0–81.0) | 77.0 (71.0–82.5) | 69.0 (62.5–75.0) | 60.5 (53.5–67.0) | 37.5 (31.0–44.0) | 92.5 (88.5–96.0) | 94.0 (90.5–97.0) |
| dev | Grounded acc % | 49.0 (42.0–56.0) | 63.5 (57.0–70.0) | 37.0 (30.5–44.0) | 56.5 (50.0–63.5) | 50.5 (43.5–57.5) | 60.0 (53.0–67.0) | 68.5 (62.0–75.0) | 61.0 (54.5–67.5) | 71.5 (65.5–77.5) | 73.0 (67.0–79.0) | 40.0 (33.5–47.0) | 36.0 (29.5–42.5) | 58.5 (51.5–65.0) | 69.0 (62.5–75.5) | 70.5 (64.0–76.5) | 73.5 (67.5–79.5) | – | 53.0 (46.0–60.0) | 28.5 (22.5–35.0) | 90.0 (86.0–94.0) | 82.5 (77.0–87.5) |
| dev | Memory-suspect % | 2.0 (0.5–4.0) | 2.0 (0.5–4.0) | 1.5 (0.0–3.5) | 1.0 (0.0–2.5) | 0.5 (0.0–1.5) | 1.0 (0.0–2.5) | 0.5 (0.0–1.5) | 1.5 (0.0–3.5) | 0.5 (0.0–1.5) | 0.5 (0.0–1.5) | 1.5 (0.0–3.5) | 1.0 (0.0–2.5) | 0.5 (0.0–1.5) | 1.5 (0.0–3.5) | 1.0 (0.0–2.5) | 0.5 (0.0–1.5) | – | 2.0 (0.5–4.0) | 2.5 (0.5–5.0) | 0.5 (0.0–1.5) | 1.5 (0.0–3.5) |
| heldout | FRAMES acc % | 52.1 (48.1–56.1) | 68.6 (64.9–72.3) | 42.8 (38.9–46.6) | 56.2 (52.4–60.1) | 54.8 (50.8–58.7) | 62.0 (58.2–65.7) | 73.1 (69.6–76.6) | 63.3 (59.6–67.0) | 75.2 (71.8–78.5) | 78.7 (75.5–81.7) | 46.2 (42.3–50.0) | 36.1 (32.4–39.7) | 58.3 (54.5–62.2) | 68.1 (64.4–71.8) | 66.8 (63.1–70.4) | 71.0 (67.5–74.5) | 63.6 (59.9–67.5) | 56.7 (52.9–60.6) | 35.9 (32.2–39.6) | 93.1 (91.0–95.0) | 92.3 (90.1–94.2) |
| heldout | Grounded acc % | 45.0 (41.2–49.0) | 60.6 (56.7–64.4) | 36.7 (32.9–40.2) | 50.0 (46.2–53.8) | 47.0 (42.9–50.8) | 55.3 (51.4–59.1) | 67.1 (63.5–70.8) | 55.4 (51.6–59.3) | 69.1 (65.4–72.6) | 73.2 (69.9–76.6) | 39.6 (35.7–43.4) | 29.5 (26.0–33.0) | 53.4 (49.4–57.4) | 61.1 (57.2–64.9) | 59.6 (55.9–63.3) | 64.1 (60.4–67.8) | – | 49.2 (45.2–53.0) | 27.4 (23.9–30.8) | 90.1 (87.7–92.3) | 84.5 (81.4–87.3) |
| heldout | Memory-suspect % | 2.2 (1.1–3.5) | 1.9 (1.0–3.0) | 1.0 (0.3–1.8) | 1.4 (0.6–2.4) | 2.2 (1.1–3.5) | 1.3 (0.5–2.2) | 1.0 (0.3–1.8) | 1.6 (0.6–2.7) | 0.8 (0.2–1.6) | 0.6 (0.2–1.3) | 2.4 (1.3–3.7) | 1.8 (0.8–2.9) | 0.6 (0.2–1.3) | 1.4 (0.6–2.4) | 1.4 (0.6–2.4) | 2.1 (1.0–3.4) | – | 1.8 (0.8–2.9) | 2.1 (1.0–3.4) | 0.2 (0.0–0.5) | 1.3 (0.5–2.2) |

### Accuracy by reasoning type

| Label | adv-dense-decompose | adv-dense-expand | adv-hybrid | adv-hybrid-decompose | adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand | adv-hybrid-expand-rerank | adv-hybrid-expand-rerank-bge | adv-hybrid-expand-s2b | adv-hybrid-rerank | adv-sparse | adv-std-hybrid | adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | closed_book | naive-std | naive_rag | oracle | pipeshub |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Multiple constraints | 49.7 | 69.4 | 42.1 | 56.5 | 53.9 | 61.6 | 72.5 | 63.9 | 75.0 | 77.8 | 44.6 | 38.8 | 57.6 | 68.3 | 67.8 | 71.2 | 64.3 | 55.9 | 34.2 | 92.9 | 93.4 |
| Numerical reasoning | 50.2 | 61.4 | 37.5 | 52.9 | 48.1 | 60.8 | 67.2 | 57.7 | 69.3 | 72.7 | 41.0 | 30.4 | 53.2 | 62.8 | 62.8 | 65.2 | 57.0 | 53.9 | 33.4 | 88.4 | 88.4 |
| Post processing | 48.6 | 63.6 | 40.2 | 54.2 | 52.3 | 62.6 | 68.2 | 60.7 | 72.9 | 76.6 | 47.7 | 34.6 | 57.0 | 64.5 | 61.7 | 63.6 | 55.1 | 51.4 | 38.3 | 90.7 | 83.2 |
| Tabular reasoning | 44.1 | 61.9 | 36.4 | 51.7 | 47.0 | 58.9 | 67.8 | 59.7 | 69.1 | 75.4 | 39.4 | 32.6 | 47.9 | 63.6 | 59.3 | 67.8 | 58.5 | 49.6 | 32.6 | 90.7 | 92.4 |
| Temporal reasoning | 45.0 | 65.1 | 36.0 | 50.4 | 48.9 | 58.3 | 70.9 | 58.3 | 74.8 | 75.2 | 40.3 | 30.6 | 56.8 | 66.9 | 65.1 | 67.6 | 60.4 | 54.7 | 28.8 | 91.4 | 90.6 |

### Accuracy by gold-article count

| Label | adv-dense-decompose | adv-dense-expand | adv-hybrid | adv-hybrid-decompose | adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand | adv-hybrid-expand-rerank | adv-hybrid-expand-rerank-bge | adv-hybrid-expand-s2b | adv-hybrid-rerank | adv-sparse | adv-std-hybrid | adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | closed_book | naive-std | naive_rag | oracle | pipeshub |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | 65.8 | 79.2 | 53.0 | 67.7 | 65.2 | 75.4 | 83.1 | 73.5 | 82.1 | 86.6 | 56.2 | 46.0 | 69.3 | 79.2 | 74.8 | 81.2 | 66.8 | 69.6 | 46.6 | 93.9 | 93.3 |
| 3 | 47.4 | 66.3 | 36.8 | 55.1 | 49.8 | 58.2 | 69.8 | 60.0 | 74.0 | 78.2 | 38.6 | 31.9 | 56.8 | 68.1 | 68.1 | 70.9 | 63.2 | 51.9 | 30.2 | 93.3 | 90.9 |
| 4 | 45.5 | 66.4 | 43.3 | 50.7 | 51.5 | 58.2 | 68.7 | 58.2 | 72.4 | 73.9 | 46.3 | 36.6 | 56.0 | 63.4 | 65.7 | 65.7 | 61.2 | 53.0 | 35.8 | 91.8 | 97.0 |
| 5+ | 34.8 | 46.7 | 30.4 | 42.4 | 42.4 | 48.9 | 58.7 | 58.7 | 62.0 | 62.0 | 35.9 | 30.4 | 45.7 | 53.3 | 55.4 | 57.6 | 69.6 | 41.3 | 20.7 | 90.2 | 90.2 |

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
| adv-dense-decompose | 379 | 37 | 18 | 0 | 0 | 100.0 |
| adv-dense-expand | 505 | 47 | 16 | 0 | 1 | 100.0 |
| adv-hybrid | 303 | 45 | 9 | 0 | 0 | 100.0 |
| adv-hybrid-decompose | 425 | 40 | 11 | 0 | 0 | 100.0 |
| adv-hybrid-decompose-rerank | 394 | 44 | 15 | 0 | 1 | 100.0 |
| adv-hybrid-decompose-rerank-s2b | 465 | 49 | 10 | 0 | 1 | 100.0 |
| adv-hybrid-expand | 556 | 42 | 7 | 0 | 0 | 100.0 |
| adv-hybrid-expand-rerank | 468 | 52 | 13 | 0 | 0 | 100.0 |
| adv-hybrid-expand-rerank-bge | 574 | 39 | 6 | 0 | 3 | 100.0 |
| adv-hybrid-expand-s2b | 603 | 37 | 5 | 0 | 5 | 100.0 |
| adv-hybrid-rerank | 327 | 36 | 18 | 0 | 0 | 100.0 |
| adv-sparse | 256 | 41 | 13 | 0 | 2 | 100.0 |
| adv-std-hybrid | 450 | 39 | 5 | 0 | 2 | 100.0 |
| adv-std-hybrid-decompose-rerank-s2b | 519 | 40 | 12 | 0 | 5 | 100.0 |
| adv-std-hybrid-expand-rerank | 513 | 43 | 11 | 0 | 0 | 100.0 |
| adv-std-hybrid-expand-rerank-s2b | 547 | 35 | 14 | 0 | 1 | 100.0 |
| naive-std | 413 | 45 | 15 | 0 | 2 | 100.0 |
| naive_rag | 228 | 53 | 18 | 0 | 0 | 100.0 |
| oracle | 742 | 19 | 2 | 0 | 3 | 100.0 |
| pipeshub | 692 | 59 | 11 | 0 | 2 | 100.0 |

Memory-suspect questions:

- **adv-dense-decompose**: 68, 71, 86, 158, 185, 230, 239, 310, 388, 415, 450, 540, 547, 618, 660, 688, 699, 708
- **adv-dense-expand**: 11, 43, 68, 82, 239, 240, 264, 310, 453, 645, 691, 702, 720, 728, 754, 803
- **adv-hybrid**: 37, 256, 296, 331, 410, 415, 688, 691, 699
- **adv-hybrid-decompose**: 27, 106, 172, 229, 239, 277, 383, 408, 447, 478, 728
- **adv-hybrid-decompose-rerank**: 7, 68, 142, 229, 289, 310, 325, 341, 384, 540, 585, 688, 699, 728, 744
- **adv-hybrid-decompose-rerank-s2b**: 106, 172, 256, 310, 358, 401, 415, 688, 728, 748
- **adv-hybrid-expand**: 86, 106, 229, 419, 444, 645, 728
- **adv-hybrid-expand-rerank**: 82, 161, 172, 229, 277, 282, 382, 401, 478, 596, 688, 699, 701
- **adv-hybrid-expand-rerank-bge**: 259, 285, 388, 408, 469, 618
- **adv-hybrid-expand-s2b**: 27, 229, 310, 410, 699
- **adv-hybrid-rerank**: 68, 115, 145, 172, 209, 285, 292, 302, 325, 388, 444, 621, 691, 699, 701, 705, 728, 759
- **adv-sparse**: 88, 106, 115, 194, 229, 282, 336, 447, 540, 621, 688, 691, 701
- **adv-std-hybrid**: 9, 27, 51, 136, 388
- **adv-std-hybrid-decompose-rerank-s2b**: 3, 9, 51, 82, 161, 342, 358, 388, 401, 539, 596, 701
- **adv-std-hybrid-expand-rerank**: 9, 27, 76, 92, 340, 520, 540, 679, 702, 777, 821
- **adv-std-hybrid-expand-rerank-s2b**: 27, 76, 88, 142, 149, 215, 310, 358, 478, 520, 539, 645, 688, 821
- **naive-std**: 7, 27, 37, 68, 71, 161, 172, 256, 291, 438, 446, 530, 720, 728, 748
- **naive_rag**: 11, 37, 53, 68, 169, 359, 410, 443, 473, 530, 547, 688, 690, 691, 701, 702, 728, 769
- **oracle**: 27, 652
- **pipeshub**: 68, 190, 231, 256, 343, 344, 501, 536, 652, 756, 815

### Paired comparisons (exact McNemar)

| A | B | A only | B only | p |
|---|---|---|---|---|
| adv-dense-decompose | adv-dense-expand | 45 | 180 | 0.0000 |
| adv-dense-decompose | adv-hybrid | 143 | 66 | 0.0000 |
| adv-dense-decompose | adv-hybrid-decompose | 61 | 103 | 0.0013 |
| adv-dense-decompose | adv-hybrid-decompose-rerank | 81 | 101 | 0.1588 |
| adv-dense-decompose | adv-hybrid-decompose-rerank-s2b | 55 | 146 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand | 35 | 206 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-rerank | 70 | 169 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-rerank-bge | 32 | 220 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-s2b | 23 | 239 | 0.0000 |
| adv-dense-decompose | adv-hybrid-rerank | 137 | 84 | 0.0004 |
| adv-dense-decompose | adv-sparse | 195 | 73 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid | 87 | 149 | 0.0001 |
| adv-dense-decompose | adv-std-hybrid-decompose-rerank-s2b | 50 | 192 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid-expand-rerank | 61 | 194 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid-expand-rerank-s2b | 50 | 213 | 0.0000 |
| adv-dense-decompose | closed_book | 111 | 212 | 0.0000 |
| adv-dense-decompose | naive-std | 91 | 132 | 0.0073 |
| adv-dense-decompose | naive_rag | 168 | 33 | 0.0000 |
| adv-dense-decompose | oracle | 11 | 343 | 0.0000 |
| adv-dense-decompose | pipeshub | 5 | 335 | 0.0000 |
| adv-dense-expand | adv-hybrid | 252 | 40 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose | 161 | 68 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose-rerank | 176 | 61 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose-rerank-s2b | 139 | 95 | 0.0048 |
| adv-dense-expand | adv-hybrid-expand | 49 | 85 | 0.0024 |
| adv-dense-expand | adv-hybrid-expand-rerank | 118 | 82 | 0.0131 |
| adv-dense-expand | adv-hybrid-expand-rerank-bge | 48 | 101 | 0.0000 |
| adv-dense-expand | adv-hybrid-expand-s2b | 35 | 116 | 0.0000 |
| adv-dense-expand | adv-hybrid-rerank | 240 | 52 | 0.0000 |
| adv-dense-expand | adv-sparse | 303 | 46 | 0.0000 |
| adv-dense-expand | adv-std-hybrid | 166 | 93 | 0.0000 |
| adv-dense-expand | adv-std-hybrid-decompose-rerank-s2b | 116 | 123 | 0.6980 |
| adv-dense-expand | adv-std-hybrid-expand-rerank | 116 | 114 | 0.9474 |
| adv-dense-expand | adv-std-hybrid-expand-rerank-s2b | 103 | 131 | 0.0773 |
| adv-dense-expand | closed_book | 136 | 102 | 0.0322 |
| adv-dense-expand | naive-std | 170 | 76 | 0.0000 |
| adv-dense-expand | naive_rag | 288 | 18 | 0.0000 |
| adv-dense-expand | oracle | 10 | 207 | 0.0000 |
| adv-dense-expand | pipeshub | 11 | 206 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose | 26 | 145 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose-rerank | 38 | 135 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose-rerank-s2b | 25 | 193 | 0.0000 |
| adv-hybrid | adv-hybrid-expand | 12 | 260 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-rerank | 41 | 217 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-rerank-bge | 16 | 281 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-s2b | 9 | 302 | 0.0000 |
| adv-hybrid | adv-hybrid-rerank | 39 | 63 | 0.0223 |
| adv-hybrid | adv-sparse | 80 | 35 | 0.0000 |
| adv-hybrid | adv-std-hybrid | 33 | 172 | 0.0000 |
| adv-hybrid | adv-std-hybrid-decompose-rerank-s2b | 24 | 243 | 0.0000 |
| adv-hybrid | adv-std-hybrid-expand-rerank | 33 | 243 | 0.0000 |
| adv-hybrid | adv-std-hybrid-expand-rerank-s2b | 23 | 263 | 0.0000 |
| adv-hybrid | closed_book | 96 | 274 | 0.0000 |
| adv-hybrid | naive-std | 50 | 168 | 0.0000 |
| adv-hybrid | naive_rag | 99 | 41 | 0.0000 |
| adv-hybrid | oracle | 4 | 413 | 0.0000 |
| adv-hybrid | pipeshub | 4 | 411 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-decompose-rerank | 84 | 62 | 0.0819 |
| adv-hybrid-decompose | adv-hybrid-decompose-rerank-s2b | 58 | 107 | 0.0002 |
| adv-hybrid-decompose | adv-hybrid-expand | 38 | 167 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-expand-rerank | 77 | 134 | 0.0001 |
| adv-hybrid-decompose | adv-hybrid-expand-rerank-bge | 31 | 177 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-expand-s2b | 28 | 202 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-rerank | 135 | 40 | 0.0000 |
| adv-hybrid-decompose | adv-sparse | 193 | 29 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid | 86 | 106 | 0.1701 |
| adv-hybrid-decompose | adv-std-hybrid-decompose-rerank-s2b | 50 | 150 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid-expand-rerank | 64 | 155 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid-expand-rerank-s2b | 48 | 169 | 0.0000 |
| adv-hybrid-decompose | closed_book | 135 | 194 | 0.0013 |
| adv-hybrid-decompose | naive-std | 109 | 108 | 1.0000 |
| adv-hybrid-decompose | naive_rag | 203 | 26 | 0.0000 |
| adv-hybrid-decompose | oracle | 9 | 299 | 0.0000 |
| adv-hybrid-decompose | pipeshub | 8 | 296 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | 32 | 103 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand | 43 | 194 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-rerank | 40 | 119 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-rerank-bge | 31 | 199 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-s2b | 29 | 225 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-rerank | 106 | 33 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-sparse | 170 | 28 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid | 71 | 113 | 0.0024 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-decompose-rerank-s2b | 42 | 164 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-expand-rerank | 42 | 155 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-expand-rerank-s2b | 39 | 182 | 0.0000 |
| adv-hybrid-decompose-rerank | closed_book | 127 | 208 | 0.0000 |
| adv-hybrid-decompose-rerank | naive-std | 92 | 113 | 0.1623 |
| adv-hybrid-decompose-rerank | naive_rag | 193 | 38 | 0.0000 |
| adv-hybrid-decompose-rerank | oracle | 5 | 317 | 0.0000 |
| adv-hybrid-decompose-rerank | pipeshub | 8 | 318 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand | 69 | 149 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank | 86 | 94 | 0.6020 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank-bge | 55 | 152 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-s2b | 36 | 161 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-rerank | 169 | 25 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-sparse | 233 | 20 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid | 104 | 75 | 0.0361 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-decompose-rerank-s2b | 47 | 98 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 65 | 107 | 0.0017 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank-s2b | 45 | 117 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | closed_book | 149 | 159 | 0.6081 |
| adv-hybrid-decompose-rerank-s2b | naive-std | 125 | 75 | 0.0005 |
| adv-hybrid-decompose-rerank-s2b | naive_rag | 251 | 25 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | oracle | 9 | 250 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | pipeshub | 10 | 249 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-expand-rerank | 122 | 50 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-expand-rerank-bge | 50 | 67 | 0.1388 |
| adv-hybrid-expand | adv-hybrid-expand-s2b | 33 | 78 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-rerank | 250 | 26 | 0.0000 |
| adv-hybrid-expand | adv-sparse | 311 | 18 | 0.0000 |
| adv-hybrid-expand | adv-std-hybrid | 175 | 66 | 0.0000 |
| adv-hybrid-expand | adv-std-hybrid-decompose-rerank-s2b | 121 | 92 | 0.0548 |
| adv-hybrid-expand | adv-std-hybrid-expand-rerank | 125 | 87 | 0.0109 |
| adv-hybrid-expand | adv-std-hybrid-expand-rerank-s2b | 98 | 90 | 0.6098 |
| adv-hybrid-expand | closed_book | 154 | 84 | 0.0000 |
| adv-hybrid-expand | naive-std | 190 | 60 | 0.0000 |
| adv-hybrid-expand | naive_rag | 323 | 17 | 0.0000 |
| adv-hybrid-expand | oracle | 12 | 173 | 0.0000 |
| adv-hybrid-expand | pipeshub | 11 | 170 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-expand-rerank-bge | 37 | 126 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-expand-s2b | 39 | 156 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-rerank | 182 | 30 | 0.0000 |
| adv-hybrid-expand-rerank | adv-sparse | 248 | 27 | 0.0000 |
| adv-hybrid-expand-rerank | adv-std-hybrid | 127 | 90 | 0.0143 |
| adv-hybrid-expand-rerank | adv-std-hybrid-decompose-rerank-s2b | 82 | 125 | 0.0034 |
| adv-hybrid-expand-rerank | adv-std-hybrid-expand-rerank | 76 | 110 | 0.0153 |
| adv-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | 64 | 128 | 0.0000 |
| adv-hybrid-expand-rerank | closed_book | 132 | 134 | 0.9511 |
| adv-hybrid-expand-rerank | naive-std | 148 | 90 | 0.0002 |
| adv-hybrid-expand-rerank | naive_rag | 272 | 38 | 0.0000 |
| adv-hybrid-expand-rerank | oracle | 9 | 242 | 0.0000 |
| adv-hybrid-expand-rerank | pipeshub | 13 | 244 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-hybrid-expand-s2b | 40 | 68 | 0.0091 |
| adv-hybrid-expand-rerank-bge | adv-hybrid-rerank | 259 | 18 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-sparse | 321 | 11 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid | 181 | 55 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-decompose-rerank-s2b | 122 | 76 | 0.0013 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-expand-rerank | 124 | 69 | 0.0001 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-expand-rerank-s2b | 99 | 74 | 0.0677 |
| adv-hybrid-expand-rerank-bge | closed_book | 168 | 81 | 0.0000 |
| adv-hybrid-expand-rerank-bge | naive-std | 196 | 49 | 0.0000 |
| adv-hybrid-expand-rerank-bge | naive_rag | 339 | 16 | 0.0000 |
| adv-hybrid-expand-rerank-bge | oracle | 14 | 158 | 0.0000 |
| adv-hybrid-expand-rerank-bge | pipeshub | 11 | 153 | 0.0000 |
| adv-hybrid-expand-s2b | adv-hybrid-rerank | 294 | 25 | 0.0000 |
| adv-hybrid-expand-s2b | adv-sparse | 350 | 12 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid | 195 | 41 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-decompose-rerank-s2b | 132 | 58 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank | 137 | 54 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank-s2b | 105 | 52 | 0.0000 |
| adv-hybrid-expand-s2b | closed_book | 178 | 63 | 0.0000 |
| adv-hybrid-expand-s2b | naive-std | 217 | 42 | 0.0000 |
| adv-hybrid-expand-s2b | naive_rag | 369 | 18 | 0.0000 |
| adv-hybrid-expand-s2b | oracle | 12 | 128 | 0.0000 |
| adv-hybrid-expand-s2b | pipeshub | 16 | 130 | 0.0000 |
| adv-hybrid-rerank | adv-sparse | 96 | 27 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid | 42 | 157 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-decompose-rerank-s2b | 29 | 224 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-expand-rerank | 28 | 214 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-expand-rerank-s2b | 25 | 241 | 0.0000 |
| adv-hybrid-rerank | closed_book | 112 | 266 | 0.0000 |
| adv-hybrid-rerank | naive-std | 55 | 149 | 0.0000 |
| adv-hybrid-rerank | naive_rag | 117 | 35 | 0.0000 |
| adv-hybrid-rerank | oracle | 9 | 394 | 0.0000 |
| adv-hybrid-rerank | pipeshub | 8 | 391 | 0.0000 |
| adv-sparse | adv-std-hybrid | 31 | 215 | 0.0000 |
| adv-sparse | adv-std-hybrid-decompose-rerank-s2b | 21 | 285 | 0.0000 |
| adv-sparse | adv-std-hybrid-expand-rerank | 24 | 279 | 0.0000 |
| adv-sparse | adv-std-hybrid-expand-rerank-s2b | 17 | 302 | 0.0000 |
| adv-sparse | closed_book | 88 | 311 | 0.0000 |
| adv-sparse | naive-std | 49 | 212 | 0.0000 |
| adv-sparse | naive_rag | 110 | 97 | 0.4043 |
| adv-sparse | oracle | 5 | 459 | 0.0000 |
| adv-sparse | pipeshub | 3 | 455 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-decompose-rerank-s2b | 39 | 119 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-expand-rerank | 40 | 111 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-expand-rerank-s2b | 38 | 139 | 0.0000 |
| adv-std-hybrid | closed_book | 138 | 177 | 0.0321 |
| adv-std-hybrid | naive-std | 75 | 54 | 0.0778 |
| adv-std-hybrid | naive_rag | 227 | 30 | 0.0000 |
| adv-std-hybrid | oracle | 9 | 279 | 0.0000 |
| adv-std-hybrid | pipeshub | 7 | 275 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 68 | 59 | 0.4779 |
| adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank-s2b | 38 | 59 | 0.0417 |
| adv-std-hybrid-decompose-rerank-s2b | closed_book | 171 | 130 | 0.0210 |
| adv-std-hybrid-decompose-rerank-s2b | naive-std | 147 | 46 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | naive_rag | 299 | 22 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | oracle | 10 | 200 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | pipeshub | 16 | 204 | 0.0000 |
| adv-std-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | 33 | 63 | 0.0029 |
| adv-std-hybrid-expand-rerank | closed_book | 172 | 140 | 0.0791 |
| adv-std-hybrid-expand-rerank | naive-std | 138 | 46 | 0.0000 |
| adv-std-hybrid-expand-rerank | naive_rag | 298 | 30 | 0.0000 |
| adv-std-hybrid-expand-rerank | oracle | 11 | 210 | 0.0000 |
| adv-std-hybrid-expand-rerank | pipeshub | 11 | 208 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | closed_book | 177 | 115 | 0.0003 |
| adv-std-hybrid-expand-rerank-s2b | naive-std | 166 | 44 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | naive_rag | 322 | 24 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | oracle | 12 | 181 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | pipeshub | 11 | 178 | 0.0000 |
| closed_book | naive-std | 193 | 133 | 0.0011 |
| closed_book | naive_rag | 319 | 83 | 0.0000 |
| closed_book | oracle | 26 | 257 | 0.0000 |
| closed_book | pipeshub | 20 | 249 | 0.0000 |
| naive-std | naive_rag | 207 | 31 | 0.0000 |
| naive-std | oracle | 9 | 300 | 0.0000 |
| naive-std | pipeshub | 6 | 295 | 0.0000 |
| naive_rag | oracle | 6 | 473 | 0.0000 |
| naive_rag | pipeshub | 6 | 471 | 0.0000 |
| oracle | pipeshub | 29 | 27 | 0.8939 |

### Paired comparisons on grounded correctness (exact McNemar)

| A | B | A only | B only | p |
|---|---|---|---|---|
| adv-dense-decompose | adv-dense-expand | 52 | 178 | 0.0000 |
| adv-dense-decompose | adv-hybrid | 145 | 69 | 0.0000 |
| adv-dense-decompose | adv-hybrid-decompose | 53 | 99 | 0.0002 |
| adv-dense-decompose | adv-hybrid-decompose-rerank | 86 | 101 | 0.3059 |
| adv-dense-decompose | adv-hybrid-decompose-rerank-s2b | 64 | 150 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand | 36 | 213 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-rerank | 75 | 164 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-rerank-bge | 36 | 231 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-s2b | 24 | 248 | 0.0000 |
| adv-dense-decompose | adv-hybrid-rerank | 139 | 87 | 0.0007 |
| adv-dense-decompose | adv-sparse | 190 | 67 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid | 85 | 156 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid-decompose-rerank-s2b | 54 | 194 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid-expand-rerank | 66 | 200 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid-expand-rerank-s2b | 56 | 224 | 0.0000 |
| adv-dense-decompose | naive-std | 100 | 134 | 0.0308 |
| adv-dense-decompose | naive_rag | 184 | 33 | 0.0000 |
| adv-dense-decompose | oracle | 14 | 377 | 0.0000 |
| adv-dense-decompose | pipeshub | 21 | 334 | 0.0000 |
| adv-dense-expand | adv-hybrid | 248 | 46 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose | 154 | 74 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose-rerank | 180 | 69 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose-rerank-s2b | 145 | 105 | 0.0135 |
| adv-dense-expand | adv-hybrid-expand | 54 | 105 | 0.0001 |
| adv-dense-expand | adv-hybrid-expand-rerank | 121 | 84 | 0.0117 |
| adv-dense-expand | adv-hybrid-expand-rerank-bge | 51 | 120 | 0.0000 |
| adv-dense-expand | adv-hybrid-expand-s2b | 35 | 133 | 0.0000 |
| adv-dense-expand | adv-hybrid-rerank | 236 | 58 | 0.0000 |
| adv-dense-expand | adv-sparse | 293 | 44 | 0.0000 |
| adv-dense-expand | adv-std-hybrid | 161 | 106 | 0.0009 |
| adv-dense-expand | adv-std-hybrid-decompose-rerank-s2b | 114 | 128 | 0.4034 |
| adv-dense-expand | adv-std-hybrid-expand-rerank | 119 | 127 | 0.6555 |
| adv-dense-expand | adv-std-hybrid-expand-rerank-s2b | 105 | 147 | 0.0097 |
| adv-dense-expand | naive-std | 171 | 79 | 0.0000 |
| adv-dense-expand | naive_rag | 300 | 23 | 0.0000 |
| adv-dense-expand | oracle | 12 | 249 | 0.0000 |
| adv-dense-expand | pipeshub | 33 | 220 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose | 27 | 149 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose-rerank | 39 | 130 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose-rerank-s2b | 32 | 194 | 0.0000 |
| adv-hybrid | adv-hybrid-expand | 17 | 270 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-rerank | 41 | 206 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-rerank-bge | 19 | 290 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-s2b | 11 | 311 | 0.0000 |
| adv-hybrid | adv-hybrid-rerank | 39 | 63 | 0.0223 |
| adv-hybrid | adv-sparse | 84 | 37 | 0.0000 |
| adv-hybrid | adv-std-hybrid | 32 | 179 | 0.0000 |
| adv-hybrid | adv-std-hybrid-decompose-rerank-s2b | 24 | 240 | 0.0000 |
| adv-hybrid | adv-std-hybrid-expand-rerank | 34 | 244 | 0.0000 |
| adv-hybrid | adv-std-hybrid-expand-rerank-s2b | 27 | 271 | 0.0000 |
| adv-hybrid | naive-std | 56 | 166 | 0.0000 |
| adv-hybrid | naive_rag | 115 | 40 | 0.0000 |
| adv-hybrid | oracle | 5 | 444 | 0.0000 |
| adv-hybrid | pipeshub | 15 | 404 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-decompose-rerank | 89 | 58 | 0.0131 |
| adv-hybrid-decompose | adv-hybrid-decompose-rerank-s2b | 72 | 112 | 0.0039 |
| adv-hybrid-decompose | adv-hybrid-expand | 41 | 172 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-expand-rerank | 87 | 130 | 0.0042 |
| adv-hybrid-decompose | adv-hybrid-expand-rerank-bge | 36 | 185 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-expand-s2b | 29 | 207 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-rerank | 142 | 44 | 0.0000 |
| adv-hybrid-decompose | adv-sparse | 199 | 30 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid | 89 | 114 | 0.0918 |
| adv-hybrid-decompose | adv-std-hybrid-decompose-rerank-s2b | 61 | 155 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid-expand-rerank | 74 | 162 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid-expand-rerank-s2b | 59 | 181 | 0.0000 |
| adv-hybrid-decompose | naive-std | 123 | 111 | 0.4722 |
| adv-hybrid-decompose | naive_rag | 220 | 23 | 0.0000 |
| adv-hybrid-decompose | oracle | 10 | 327 | 0.0000 |
| adv-hybrid-decompose | pipeshub | 31 | 298 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | 38 | 109 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand | 41 | 203 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-rerank | 44 | 118 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-rerank-bge | 32 | 212 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-s2b | 27 | 236 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-rerank | 106 | 39 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-sparse | 171 | 33 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid | 71 | 127 | 0.0001 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-decompose-rerank-s2b | 39 | 164 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-expand-rerank | 49 | 168 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-expand-rerank-s2b | 42 | 195 | 0.0000 |
| adv-hybrid-decompose-rerank | naive-std | 100 | 119 | 0.2238 |
| adv-hybrid-decompose-rerank | naive_rag | 202 | 36 | 0.0000 |
| adv-hybrid-decompose-rerank | oracle | 6 | 354 | 0.0000 |
| adv-hybrid-decompose-rerank | pipeshub | 29 | 327 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand | 72 | 163 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank | 93 | 96 | 0.8844 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank-bge | 62 | 171 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-s2b | 36 | 174 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-rerank | 168 | 30 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-sparse | 239 | 30 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid | 106 | 91 | 0.3185 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-decompose-rerank-s2b | 52 | 106 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 69 | 117 | 0.0005 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank-s2b | 50 | 132 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | naive-std | 130 | 78 | 0.0004 |
| adv-hybrid-decompose-rerank-s2b | naive_rag | 262 | 25 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | oracle | 10 | 287 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | pipeshub | 34 | 261 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-expand-rerank | 138 | 50 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-expand-rerank-bge | 57 | 75 | 0.1387 |
| adv-hybrid-expand | adv-hybrid-expand-s2b | 39 | 86 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-rerank | 257 | 28 | 0.0000 |
| adv-hybrid-expand | adv-sparse | 319 | 19 | 0.0000 |
| adv-hybrid-expand | adv-std-hybrid | 176 | 70 | 0.0000 |
| adv-hybrid-expand | adv-std-hybrid-decompose-rerank-s2b | 132 | 95 | 0.0167 |
| adv-hybrid-expand | adv-std-hybrid-expand-rerank | 139 | 96 | 0.0060 |
| adv-hybrid-expand | adv-std-hybrid-expand-rerank-s2b | 116 | 107 | 0.5923 |
| adv-hybrid-expand | naive-std | 207 | 64 | 0.0000 |
| adv-hybrid-expand | naive_rag | 346 | 18 | 0.0000 |
| adv-hybrid-expand | oracle | 15 | 201 | 0.0000 |
| adv-hybrid-expand | pipeshub | 36 | 172 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-expand-rerank-bge | 32 | 138 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-expand-s2b | 37 | 172 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-rerank | 173 | 32 | 0.0000 |
| adv-hybrid-expand-rerank | adv-sparse | 243 | 31 | 0.0000 |
| adv-hybrid-expand-rerank | adv-std-hybrid | 121 | 103 | 0.2560 |
| adv-hybrid-expand-rerank | adv-std-hybrid-decompose-rerank-s2b | 84 | 135 | 0.0007 |
| adv-hybrid-expand-rerank | adv-std-hybrid-expand-rerank | 80 | 125 | 0.0020 |
| adv-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | 70 | 149 | 0.0000 |
| adv-hybrid-expand-rerank | naive-std | 150 | 95 | 0.0005 |
| adv-hybrid-expand-rerank | naive_rag | 274 | 34 | 0.0000 |
| adv-hybrid-expand-rerank | oracle | 7 | 281 | 0.0000 |
| adv-hybrid-expand-rerank | pipeshub | 33 | 257 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-hybrid-expand-s2b | 53 | 82 | 0.0156 |
| adv-hybrid-expand-rerank-bge | adv-hybrid-rerank | 268 | 21 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-sparse | 332 | 14 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid | 186 | 62 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-decompose-rerank-s2b | 138 | 83 | 0.0003 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-expand-rerank | 144 | 83 | 0.0001 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-expand-rerank-s2b | 118 | 91 | 0.0719 |
| adv-hybrid-expand-rerank-bge | naive-std | 218 | 57 | 0.0000 |
| adv-hybrid-expand-rerank-bge | naive_rag | 367 | 21 | 0.0000 |
| adv-hybrid-expand-rerank-bge | oracle | 20 | 188 | 0.0000 |
| adv-hybrid-expand-rerank-bge | pipeshub | 39 | 157 | 0.0000 |
| adv-hybrid-expand-s2b | adv-hybrid-rerank | 297 | 21 | 0.0000 |
| adv-hybrid-expand-s2b | adv-sparse | 360 | 13 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid | 197 | 44 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-decompose-rerank-s2b | 142 | 58 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank | 152 | 62 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank-s2b | 122 | 66 | 0.0001 |
| adv-hybrid-expand-s2b | naive-std | 230 | 40 | 0.0000 |
| adv-hybrid-expand-s2b | naive_rag | 390 | 15 | 0.0000 |
| adv-hybrid-expand-s2b | oracle | 17 | 156 | 0.0000 |
| adv-hybrid-expand-s2b | pipeshub | 45 | 134 | 0.0000 |
| adv-hybrid-rerank | adv-sparse | 98 | 27 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid | 39 | 162 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-decompose-rerank-s2b | 27 | 219 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-expand-rerank | 31 | 217 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-expand-rerank-s2b | 27 | 247 | 0.0000 |
| adv-hybrid-rerank | naive-std | 62 | 148 | 0.0000 |
| adv-hybrid-rerank | naive_rag | 135 | 36 | 0.0000 |
| adv-hybrid-rerank | oracle | 7 | 422 | 0.0000 |
| adv-hybrid-rerank | pipeshub | 23 | 388 | 0.0000 |
| adv-sparse | adv-std-hybrid | 28 | 222 | 0.0000 |
| adv-sparse | adv-std-hybrid-decompose-rerank-s2b | 20 | 283 | 0.0000 |
| adv-sparse | adv-std-hybrid-expand-rerank | 23 | 280 | 0.0000 |
| adv-sparse | adv-std-hybrid-expand-rerank-s2b | 20 | 311 | 0.0000 |
| adv-sparse | naive-std | 48 | 205 | 0.0000 |
| adv-sparse | naive_rag | 110 | 82 | 0.0511 |
| adv-sparse | oracle | 4 | 490 | 0.0000 |
| adv-sparse | pipeshub | 12 | 448 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-decompose-rerank-s2b | 45 | 114 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-expand-rerank | 48 | 111 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-expand-rerank-s2b | 42 | 139 | 0.0000 |
| adv-std-hybrid | naive-std | 91 | 54 | 0.0027 |
| adv-std-hybrid | naive_rag | 251 | 29 | 0.0000 |
| adv-std-hybrid | oracle | 11 | 303 | 0.0000 |
| adv-std-hybrid | pipeshub | 31 | 273 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 69 | 63 | 0.6636 |
| adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank-s2b | 42 | 70 | 0.0104 |
| adv-std-hybrid-decompose-rerank-s2b | naive-std | 153 | 47 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | naive_rag | 309 | 18 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | oracle | 13 | 236 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | pipeshub | 46 | 219 | 0.0000 |
| adv-std-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | 38 | 72 | 0.0015 |
| adv-std-hybrid-expand-rerank | naive-std | 149 | 49 | 0.0000 |
| adv-std-hybrid-expand-rerank | naive_rag | 313 | 28 | 0.0000 |
| adv-std-hybrid-expand-rerank | oracle | 15 | 244 | 0.0000 |
| adv-std-hybrid-expand-rerank | pipeshub | 41 | 220 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | naive-std | 179 | 45 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | naive_rag | 345 | 26 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | oracle | 18 | 213 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | pipeshub | 42 | 187 | 0.0000 |
| naive-std | naive_rag | 212 | 27 | 0.0000 |
| naive-std | oracle | 11 | 340 | 0.0000 |
| naive-std | pipeshub | 30 | 309 | 0.0000 |
| naive_rag | oracle | 5 | 519 | 0.0000 |
| naive_rag | pipeshub | 13 | 477 | 0.0000 |
| oracle | pipeshub | 79 | 29 | 0.0000 |

Judge agreement (Cohen's κ, primary vs secondary): adv-dense-decompose: 0.971, adv-dense-expand: 0.958, adv-hybrid: 0.955, adv-hybrid-decompose: 0.965, adv-hybrid-decompose-rerank: 0.971, adv-hybrid-decompose-rerank-s2b: 0.979, adv-hybrid-expand: 0.957, adv-hybrid-expand-rerank: 0.979, adv-hybrid-expand-rerank-bge: 0.949, adv-hybrid-expand-s2b: 0.975, adv-hybrid-rerank: 0.976, adv-sparse: 0.958, adv-std-hybrid: 0.962, adv-std-hybrid-decompose-rerank-s2b: 0.974, adv-std-hybrid-expand-rerank: 0.958, adv-std-hybrid-expand-rerank-s2b: 0.970, closed_book: 0.984, naive-std: 0.965, naive_rag: 0.958, oracle: 0.930, pipeshub: 0.982


# View: held-out split

## FRAMES benchmark — `held-out split`

Status: VALID

### Board

| System | FRAMES acc % (95% CI) | Grounded acc % (95% CI) | Memory-suspect | Strict % | All gold in context % | Context recall % | Citation integrity % | ALCE recall % | Correct ∧ grounded % | p95 latency s | LLM calls / q | Input tok / q | Output tok / q | Cost / q | Cost / correct |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| oracle | 93.1 (91.0–95.0) | 90.1 (87.7–92.3) | 1 (0.2%) | 92.0 (89.7–94.1) | 99.7 (99.2–100.0) | 99.9 (99.8–100.0) | – | – | – | 6.5 | 1.0 | 22,837 | 235 | $0.0048 | $0.0052 |
| pipeshub | 92.3 (90.1–94.2) | 84.5 (81.4–87.3) | 8 (1.3%) | 91.7 (89.4–93.8) | 84.1 (81.2–86.9) | 94.6 (93.5–95.6) | 98.7 | 42.4 | 18.0 | 133.5 | 2.6 | 140,720 | 860 | $0.0173 | $0.0187 |
| adv-hybrid-expand-s2b | 78.7 (75.5–81.7) | 73.2 (69.9–76.6) | 4 (0.6%) | 77.9 (74.7–81.1) | 75.2 (71.8–78.5) | 90.2 (88.7–91.6) | 93.6 | 26.7 | 6.3 | 54.2 | 2.0 | 24,844 | 574 | $0.0056 | $0.0072 |
| adv-hybrid-expand-rerank-bge | 75.2 (71.8–78.5) | 69.1 (65.4–72.6) | 5 (0.8%) | 73.7 (70.2–77.1) | 79.0 (75.6–82.1) | 92.1 (90.7–93.3) | 79.8 | 39.4 | 10.8 | 329.1 | 2.0 | 6,894 | 681 | $0.0022 | $0.0029 |
| adv-hybrid-expand | 73.1 (69.6–76.6) | 67.1 (63.5–70.8) | 6 (1.0%) | 72.4 (68.9–75.8) | 74.8 (71.5–78.0) | 89.8 (88.2–91.2) | 82.2 | 37.1 | 10.1 | 25.4 | 2.0 | 5,438 | 665 | $0.0019 | $0.0026 |
| adv-std-hybrid-expand-rerank-s2b | 71.0 (67.5–74.5) | 64.1 (60.4–67.8) | 13 (2.1%) | 70.4 (66.8–73.9) | 72.8 (69.2–76.1) | 89.3 (87.8–90.8) | 100.0 | 27.4 | 7.0 | 35.3 | 2.0 | 36,355 | 631 | $0.0080 | $0.0113 |
| adv-dense-expand | 68.6 (64.9–72.3) | 60.6 (56.7–64.4) | 12 (1.9%) | 67.5 (63.8–71.2) | 64.9 (61.1–68.6) | 85.1 (83.3–86.8) | 92.6 | 37.3 | 9.3 | 25.9 | 2.0 | 3,946 | 705 | $0.0016 | $0.0024 |
| adv-std-hybrid-decompose-rerank-s2b | 68.1 (64.4–71.8) | 61.1 (57.2–64.9) | 9 (1.4%) | 66.7 (63.0–70.4) | 70.5 (67.0–74.0) | 88.6 (87.0–90.1) | 100.0 | 26.7 | 4.5 | 24.9 | 2.0 | 36,919 | 624 | $0.0081 | $0.0119 |
| adv-std-hybrid-expand-rerank | 66.8 (63.1–70.4) | 59.6 (55.9–63.3) | 9 (1.4%) | 65.1 (61.4–68.8) | 72.6 (69.1–76.0) | 89.6 (88.1–91.0) | 100.0 | 35.6 | 7.9 | 43.5 | 2.0 | 19,253 | 664 | $0.0046 | $0.0069 |
| closed_book | 63.6 (59.9–67.5) | – | – | 63.1 (59.5–66.8) | 0.0 (0.0–0.0) | 0.0 (0.0–0.0) | – | – | – | 96.9 | 1.0 | 81 | 2,555 | $0.0031 | $0.0048 |
| adv-hybrid-expand-rerank | 63.3 (59.6–67.0) | 55.4 (51.6–59.3) | 10 (1.6%) | 62.2 (58.3–65.9) | 73.6 (70.0–76.9) | 89.9 (88.4–91.4) | 80.0 | 37.7 | 8.8 | 106.6 | 2.0 | 5,681 | 691 | $0.0020 | $0.0031 |
| adv-hybrid-decompose-rerank-s2b | 62.0 (58.2–65.7) | 55.3 (51.4–59.1) | 8 (1.3%) | 60.9 (57.1–64.6) | 66.2 (62.5–69.9) | 86.3 (84.6–87.9) | 92.3 | 27.4 | 4.2 | 32.4 | 2.0 | 26,473 | 657 | $0.0061 | $0.0098 |
| adv-std-hybrid | 58.3 (54.5–62.2) | 53.4 (49.4–57.4) | 4 (0.6%) | 56.9 (53.0–60.6) | 64.6 (60.9–68.3) | 85.2 (83.4–86.9) | 100.0 | 35.0 | 7.3 | 15.1 | 1.0 | 19,570 | 502 | $0.0045 | $0.0077 |
| naive-std | 56.7 (52.9–60.6) | 49.2 (45.2–53.0) | 11 (1.8%) | 55.0 (51.1–58.8) | 54.0 (50.0–57.9) | 78.9 (76.8–80.8) | 100.0 | 34.2 | 6.4 | 17.2 | 1.0 | 19,107 | 537 | $0.0045 | $0.0079 |
| adv-hybrid-decompose | 56.2 (52.4–60.1) | 50.0 (46.2–53.8) | 9 (1.4%) | 54.6 (50.8–58.5) | 68.6 (65.1–72.1) | 87.5 (85.9–89.1) | 82.1 | 38.8 | 8.7 | 32.5 | 2.0 | 6,263 | 731 | $0.0021 | $0.0038 |
| adv-hybrid-decompose-rerank | 54.8 (50.8–58.7) | 47.0 (42.9–50.8) | 14 (2.2%) | 54.2 (50.2–57.9) | 67.6 (63.9–71.3) | 87.2 (85.5–88.8) | 79.6 | 37.6 | 6.0 | 145.4 | 2.0 | 6,018 | 685 | $0.0020 | $0.0037 |
| adv-dense-decompose | 52.1 (48.1–56.1) | 45.0 (41.2–49.0) | 14 (2.2%) | 51.1 (47.3–55.0) | 60.4 (56.6–64.3) | 82.4 (80.5–84.3) | 89.9 | 35.9 | 5.7 | 35.8 | 2.0 | 4,523 | 808 | $0.0019 | $0.0036 |
| adv-hybrid-rerank | 46.2 (42.3–50.0) | 39.6 (35.7–43.4) | 15 (2.4%) | 46.2 (42.3–50.0) | 55.1 (51.3–59.0) | 80.3 (78.4–82.2) | 78.0 | 37.1 | 4.1 | 20.1 | 1.0 | 6,203 | 532 | $0.0019 | $0.0041 |
| adv-hybrid | 42.8 (38.9–46.6) | 36.7 (32.9–40.2) | 6 (1.0%) | 41.5 (37.7–45.2) | 52.7 (48.9–56.6) | 78.9 (76.9–80.8) | 79.6 | 34.5 | 4.3 | 18.0 | 1.0 | 5,394 | 539 | $0.0017 | $0.0040 |
| adv-sparse | 36.1 (32.4–39.7) | 29.5 (26.0–33.0) | 11 (1.8%) | 34.8 (31.1–38.5) | 46.5 (42.5–50.3) | 74.7 (72.6–76.8) | 73.9 | 34.1 | 3.9 | 13.4 | 1.0 | 6,990 | 492 | $0.0020 | $0.0055 |
| naive_rag | 35.9 (32.2–39.6) | 27.4 (23.9–30.8) | 13 (2.1%) | 34.5 (30.8–38.1) | 35.9 (32.1–39.6) | 67.2 (64.9–69.4) | 87.7 | 34.9 | 3.2 | 18.1 | 1.0 | 3,692 | 592 | $0.0014 | $0.0040 |

### Accuracy by reasoning type

| Label | adv-dense-decompose | adv-dense-expand | adv-hybrid | adv-hybrid-decompose | adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand | adv-hybrid-expand-rerank | adv-hybrid-expand-rerank-bge | adv-hybrid-expand-s2b | adv-hybrid-rerank | adv-sparse | adv-std-hybrid | adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | closed_book | naive-std | naive_rag | oracle | pipeshub |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Multiple constraints | 49.6 | 69.1 | 41.5 | 54.7 | 53.7 | 60.0 | 72.7 | 62.4 | 74.6 | 77.5 | 44.6 | 36.5 | 55.6 | 66.7 | 65.7 | 69.8 | 63.5 | 54.4 | 33.6 | 93.0 | 93.0 |
| Numerical reasoning | 49.3 | 61.1 | 38.9 | 51.6 | 48.4 | 59.7 | 67.9 | 57.0 | 69.2 | 73.3 | 41.2 | 29.9 | 52.5 | 61.1 | 61.1 | 64.3 | 54.3 | 53.8 | 32.6 | 89.1 | 87.8 |
| Post processing | 47.5 | 62.5 | 42.5 | 53.8 | 52.5 | 60.0 | 67.5 | 60.0 | 73.8 | 78.8 | 48.8 | 35.0 | 53.8 | 63.7 | 58.8 | 63.7 | 53.8 | 48.8 | 38.8 | 92.5 | 82.5 |
| Tabular reasoning | 43.6 | 61.5 | 33.0 | 47.5 | 46.4 | 59.8 | 66.5 | 58.7 | 68.7 | 75.4 | 37.4 | 26.8 | 45.8 | 61.5 | 58.1 | 66.5 | 57.5 | 48.0 | 29.6 | 91.1 | 91.1 |
| Temporal reasoning | 44.3 | 66.0 | 35.4 | 51.4 | 50.9 | 58.0 | 71.7 | 59.0 | 75.9 | 77.4 | 42.0 | 30.7 | 55.7 | 66.5 | 64.2 | 67.5 | 58.0 | 56.1 | 28.8 | 92.0 | 90.1 |

### Accuracy by gold-article count

| Label | adv-dense-decompose | adv-dense-expand | adv-hybrid | adv-hybrid-decompose | adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand | adv-hybrid-expand-rerank | adv-hybrid-expand-rerank-bge | adv-hybrid-expand-s2b | adv-hybrid-rerank | adv-sparse | adv-std-hybrid | adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | closed_book | naive-std | naive_rag | oracle | pipeshub |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | 65.0 | 77.9 | 54.2 | 68.3 | 67.1 | 75.4 | 82.9 | 72.9 | 83.3 | 87.1 | 59.6 | 45.8 | 69.6 | 78.3 | 73.3 | 80.8 | 65.8 | 70.8 | 47.9 | 93.3 | 94.2 |
| 3 | 47.1 | 66.1 | 37.4 | 53.7 | 49.8 | 57.7 | 70.5 | 57.7 | 72.7 | 78.4 | 38.3 | 30.0 | 53.7 | 67.0 | 65.6 | 68.7 | 63.4 | 50.7 | 29.5 | 94.3 | 89.9 |
| 4 | 43.8 | 68.5 | 38.2 | 47.2 | 50.6 | 53.9 | 68.5 | 59.6 | 71.9 | 74.2 | 42.7 | 33.7 | 52.8 | 58.4 | 65.2 | 65.2 | 55.1 | 49.4 | 34.8 | 92.1 | 95.5 |
| 5+ | 33.8 | 44.1 | 26.5 | 33.8 | 33.8 | 39.7 | 52.9 | 52.9 | 58.8 | 55.9 | 29.4 | 25.0 | 41.2 | 48.5 | 50.0 | 51.5 | 67.6 | 36.8 | 16.2 | 89.7 | 89.7 |

### Failure signatures

**pipeshub** (48 wrong answers)

- `F6_reasoning_miss`: 34
- `abstained`: 8
- `F5_links_unused`: 4
- `F1_stopped_early`: 2

### Evidence verification

Every answer the primary judge marked correct is checked against the context its system showed the answering model. *Memory-suspect*: judged correct, but the facts it depends on are not in that context. A system shown no context (closed book) lands every correct answer there.

| System | Supported | Partial | Memory-suspect | No evidence | Unparseable | Verified % |
|---|---|---|---|---|---|---|
| adv-dense-decompose | 281 | 30 | 14 | 0 | 0 | 100.0 |
| adv-dense-expand | 378 | 37 | 12 | 0 | 1 | 100.0 |
| adv-hybrid | 229 | 32 | 6 | 0 | 0 | 100.0 |
| adv-hybrid-decompose | 312 | 30 | 9 | 0 | 0 | 100.0 |
| adv-hybrid-decompose-rerank | 293 | 35 | 14 | 0 | 0 | 100.0 |
| adv-hybrid-decompose-rerank-s2b | 345 | 33 | 8 | 0 | 1 | 100.0 |
| adv-hybrid-expand | 419 | 31 | 6 | 0 | 0 | 100.0 |
| adv-hybrid-expand-rerank | 346 | 39 | 10 | 0 | 0 | 100.0 |
| adv-hybrid-expand-rerank-bge | 431 | 31 | 5 | 0 | 2 | 100.0 |
| adv-hybrid-expand-s2b | 457 | 25 | 4 | 0 | 5 | 100.0 |
| adv-hybrid-rerank | 247 | 26 | 15 | 0 | 0 | 100.0 |
| adv-sparse | 184 | 28 | 11 | 0 | 2 | 100.0 |
| adv-std-hybrid | 333 | 26 | 4 | 0 | 1 | 100.0 |
| adv-std-hybrid-decompose-rerank-s2b | 381 | 30 | 9 | 0 | 5 | 100.0 |
| adv-std-hybrid-expand-rerank | 372 | 36 | 9 | 0 | 0 | 100.0 |
| adv-std-hybrid-expand-rerank-s2b | 400 | 29 | 13 | 0 | 1 | 100.0 |
| naive-std | 307 | 35 | 11 | 0 | 1 | 100.0 |
| naive_rag | 171 | 40 | 13 | 0 | 0 | 100.0 |
| oracle | 562 | 15 | 1 | 0 | 3 | 100.0 |
| pipeshub | 527 | 39 | 8 | 0 | 2 | 100.0 |

Memory-suspect questions:

- **adv-dense-decompose**: 68, 71, 185, 230, 239, 310, 388, 450, 540, 618, 660, 688, 699, 708
- **adv-dense-expand**: 43, 68, 239, 240, 264, 310, 453, 645, 691, 728, 754, 803
- **adv-hybrid**: 37, 256, 331, 688, 691, 699
- **adv-hybrid-decompose**: 27, 106, 229, 239, 277, 383, 447, 478, 728
- **adv-hybrid-decompose-rerank**: 68, 142, 229, 289, 310, 325, 341, 384, 540, 585, 688, 699, 728, 744
- **adv-hybrid-decompose-rerank-s2b**: 106, 256, 310, 358, 401, 688, 728, 748
- **adv-hybrid-expand**: 106, 229, 419, 444, 645, 728
- **adv-hybrid-expand-rerank**: 161, 229, 277, 282, 382, 401, 478, 688, 699, 701
- **adv-hybrid-expand-rerank-bge**: 259, 285, 388, 469, 618
- **adv-hybrid-expand-s2b**: 27, 229, 310, 699
- **adv-hybrid-rerank**: 68, 115, 145, 209, 285, 292, 325, 388, 444, 621, 691, 699, 701, 705, 728
- **adv-sparse**: 88, 106, 115, 229, 282, 447, 540, 621, 688, 691, 701
- **adv-std-hybrid**: 9, 27, 136, 388
- **adv-std-hybrid-decompose-rerank-s2b**: 3, 9, 161, 342, 358, 388, 401, 539, 701
- **adv-std-hybrid-expand-rerank**: 9, 27, 76, 92, 340, 520, 540, 679, 821
- **adv-std-hybrid-expand-rerank-s2b**: 27, 76, 88, 142, 149, 310, 358, 478, 520, 539, 645, 688, 821
- **naive-std**: 27, 37, 68, 71, 161, 256, 291, 438, 530, 728, 748
- **naive_rag**: 37, 53, 68, 169, 359, 443, 530, 688, 690, 691, 701, 728, 769
- **oracle**: 27
- **pipeshub**: 68, 190, 256, 343, 344, 536, 756, 815

### Paired comparisons (exact McNemar)

| A | B | A only | B only | p |
|---|---|---|---|---|
| adv-dense-decompose | adv-dense-expand | 34 | 137 | 0.0000 |
| adv-dense-decompose | adv-hybrid | 104 | 46 | 0.0000 |
| adv-dense-decompose | adv-hybrid-decompose | 48 | 74 | 0.0232 |
| adv-dense-decompose | adv-hybrid-decompose-rerank | 56 | 73 | 0.1587 |
| adv-dense-decompose | adv-hybrid-decompose-rerank-s2b | 40 | 102 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand | 28 | 159 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-rerank | 51 | 121 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-rerank-bge | 22 | 166 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-s2b | 15 | 181 | 0.0000 |
| adv-dense-decompose | adv-hybrid-rerank | 98 | 61 | 0.0042 |
| adv-dense-decompose | adv-sparse | 147 | 47 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid | 68 | 107 | 0.0039 |
| adv-dense-decompose | adv-std-hybrid-decompose-rerank-s2b | 41 | 141 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid-expand-rerank | 48 | 140 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid-expand-rerank-s2b | 38 | 156 | 0.0000 |
| adv-dense-decompose | closed_book | 83 | 155 | 0.0000 |
| adv-dense-decompose | naive-std | 72 | 101 | 0.0330 |
| adv-dense-decompose | naive_rag | 125 | 24 | 0.0000 |
| adv-dense-decompose | oracle | 6 | 262 | 0.0000 |
| adv-dense-decompose | pipeshub | 4 | 255 | 0.0000 |
| adv-dense-expand | adv-hybrid | 189 | 28 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose | 124 | 47 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose-rerank | 133 | 47 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose-rerank-s2b | 110 | 69 | 0.0027 |
| adv-dense-expand | adv-hybrid-expand | 40 | 68 | 0.0091 |
| adv-dense-expand | adv-hybrid-expand-rerank | 94 | 61 | 0.0099 |
| adv-dense-expand | adv-hybrid-expand-rerank-bge | 37 | 78 | 0.0002 |
| adv-dense-expand | adv-hybrid-expand-s2b | 23 | 86 | 0.0000 |
| adv-dense-expand | adv-hybrid-rerank | 177 | 37 | 0.0000 |
| adv-dense-expand | adv-sparse | 234 | 31 | 0.0000 |
| adv-dense-expand | adv-std-hybrid | 131 | 67 | 0.0000 |
| adv-dense-expand | adv-std-hybrid-decompose-rerank-s2b | 92 | 89 | 0.8819 |
| adv-dense-expand | adv-std-hybrid-expand-rerank | 94 | 83 | 0.4524 |
| adv-dense-expand | adv-std-hybrid-expand-rerank-s2b | 80 | 95 | 0.2899 |
| adv-dense-expand | closed_book | 104 | 73 | 0.0239 |
| adv-dense-expand | naive-std | 130 | 56 | 0.0000 |
| adv-dense-expand | naive_rag | 218 | 14 | 0.0000 |
| adv-dense-expand | oracle | 7 | 160 | 0.0000 |
| adv-dense-expand | pipeshub | 9 | 157 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose | 22 | 106 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose-rerank | 25 | 100 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose-rerank-s2b | 21 | 141 | 0.0000 |
| adv-hybrid | adv-hybrid-expand | 11 | 200 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-rerank | 31 | 159 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-rerank-bge | 13 | 215 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-s2b | 8 | 232 | 0.0000 |
| adv-hybrid | adv-hybrid-rerank | 26 | 47 | 0.0186 |
| adv-hybrid | adv-sparse | 66 | 24 | 0.0000 |
| adv-hybrid | adv-std-hybrid | 25 | 122 | 0.0000 |
| adv-hybrid | adv-std-hybrid-decompose-rerank-s2b | 22 | 180 | 0.0000 |
| adv-hybrid | adv-std-hybrid-expand-rerank | 22 | 172 | 0.0000 |
| adv-hybrid | adv-std-hybrid-expand-rerank-s2b | 17 | 193 | 0.0000 |
| adv-hybrid | closed_book | 70 | 200 | 0.0000 |
| adv-hybrid | naive-std | 37 | 124 | 0.0000 |
| adv-hybrid | naive_rag | 71 | 28 | 0.0000 |
| adv-hybrid | oracle | 3 | 317 | 0.0000 |
| adv-hybrid | pipeshub | 4 | 313 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-decompose-rerank | 59 | 50 | 0.4437 |
| adv-hybrid-decompose | adv-hybrid-decompose-rerank-s2b | 43 | 79 | 0.0014 |
| adv-hybrid-decompose | adv-hybrid-expand | 28 | 133 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-expand-rerank | 57 | 101 | 0.0006 |
| adv-hybrid-decompose | adv-hybrid-expand-rerank-bge | 22 | 140 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-expand-s2b | 19 | 159 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-rerank | 98 | 35 | 0.0000 |
| adv-hybrid-decompose | adv-sparse | 150 | 24 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid | 65 | 78 | 0.3156 |
| adv-hybrid-decompose | adv-std-hybrid-decompose-rerank-s2b | 40 | 114 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid-expand-rerank | 47 | 113 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid-expand-rerank-s2b | 37 | 129 | 0.0000 |
| adv-hybrid-decompose | closed_book | 97 | 143 | 0.0036 |
| adv-hybrid-decompose | naive-std | 83 | 86 | 0.8778 |
| adv-hybrid-decompose | naive_rag | 147 | 20 | 0.0000 |
| adv-hybrid-decompose | oracle | 4 | 234 | 0.0000 |
| adv-hybrid-decompose | pipeshub | 6 | 231 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | 23 | 68 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand | 35 | 149 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-rerank | 32 | 85 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-rerank-bge | 23 | 150 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-s2b | 21 | 170 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-rerank | 78 | 24 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-sparse | 132 | 15 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid | 55 | 77 | 0.0672 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-decompose-rerank-s2b | 34 | 117 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-expand-rerank | 34 | 109 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-expand-rerank-s2b | 32 | 133 | 0.0000 |
| adv-hybrid-decompose-rerank | closed_book | 95 | 150 | 0.0005 |
| adv-hybrid-decompose-rerank | naive-std | 73 | 85 | 0.3816 |
| adv-hybrid-decompose-rerank | naive_rag | 143 | 25 | 0.0000 |
| adv-hybrid-decompose-rerank | oracle | 4 | 243 | 0.0000 |
| adv-hybrid-decompose-rerank | pipeshub | 7 | 241 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand | 52 | 121 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank | 61 | 69 | 0.5394 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank-bge | 39 | 121 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-s2b | 27 | 131 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-rerank | 120 | 21 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-sparse | 177 | 15 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid | 77 | 54 | 0.0542 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-decompose-rerank-s2b | 35 | 73 | 0.0003 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 50 | 80 | 0.0107 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank-s2b | 33 | 89 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | closed_book | 109 | 119 | 0.5512 |
| adv-hybrid-decompose-rerank-s2b | naive-std | 89 | 56 | 0.0077 |
| adv-hybrid-decompose-rerank-s2b | naive_rag | 183 | 20 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | oracle | 6 | 200 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | pipeshub | 8 | 197 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-expand-rerank | 101 | 40 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-expand-rerank-bge | 39 | 52 | 0.2082 |
| adv-hybrid-expand | adv-hybrid-expand-s2b | 23 | 58 | 0.0001 |
| adv-hybrid-expand | adv-hybrid-rerank | 188 | 20 | 0.0000 |
| adv-hybrid-expand | adv-sparse | 243 | 12 | 0.0000 |
| adv-hybrid-expand | adv-std-hybrid | 140 | 48 | 0.0000 |
| adv-hybrid-expand | adv-std-hybrid-decompose-rerank-s2b | 99 | 68 | 0.0200 |
| adv-hybrid-expand | adv-std-hybrid-expand-rerank | 101 | 62 | 0.0028 |
| adv-hybrid-expand | adv-std-hybrid-expand-rerank-s2b | 78 | 65 | 0.3156 |
| adv-hybrid-expand | closed_book | 118 | 59 | 0.0000 |
| adv-hybrid-expand | naive-std | 147 | 45 | 0.0000 |
| adv-hybrid-expand | naive_rag | 246 | 14 | 0.0000 |
| adv-hybrid-expand | oracle | 10 | 135 | 0.0000 |
| adv-hybrid-expand | pipeshub | 9 | 129 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-expand-rerank-bge | 28 | 102 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-expand-s2b | 28 | 124 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-rerank | 131 | 24 | 0.0000 |
| adv-hybrid-expand-rerank | adv-sparse | 188 | 18 | 0.0000 |
| adv-hybrid-expand-rerank | adv-std-hybrid | 97 | 66 | 0.0185 |
| adv-hybrid-expand-rerank | adv-std-hybrid-decompose-rerank-s2b | 65 | 95 | 0.0216 |
| adv-hybrid-expand-rerank | adv-std-hybrid-expand-rerank | 62 | 84 | 0.0819 |
| adv-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | 51 | 99 | 0.0001 |
| adv-hybrid-expand-rerank | closed_book | 101 | 103 | 0.9442 |
| adv-hybrid-expand-rerank | naive-std | 109 | 68 | 0.0025 |
| adv-hybrid-expand-rerank | naive_rag | 199 | 28 | 0.0000 |
| adv-hybrid-expand-rerank | oracle | 6 | 192 | 0.0000 |
| adv-hybrid-expand-rerank | pipeshub | 10 | 191 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-hybrid-expand-s2b | 32 | 54 | 0.0230 |
| adv-hybrid-expand-rerank-bge | adv-hybrid-rerank | 195 | 14 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-sparse | 251 | 7 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid | 144 | 39 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-decompose-rerank-s2b | 98 | 54 | 0.0004 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-expand-rerank | 102 | 50 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-expand-rerank-s2b | 78 | 52 | 0.0279 |
| adv-hybrid-expand-rerank-bge | closed_book | 133 | 61 | 0.0000 |
| adv-hybrid-expand-rerank-bge | naive-std | 150 | 35 | 0.0000 |
| adv-hybrid-expand-rerank-bge | naive_rag | 256 | 11 | 0.0000 |
| adv-hybrid-expand-rerank-bge | oracle | 10 | 122 | 0.0000 |
| adv-hybrid-expand-rerank-bge | pipeshub | 11 | 118 | 0.0000 |
| adv-hybrid-expand-s2b | adv-hybrid-rerank | 223 | 20 | 0.0000 |
| adv-hybrid-expand-s2b | adv-sparse | 275 | 9 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid | 157 | 30 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-decompose-rerank-s2b | 110 | 44 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank | 115 | 41 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank-s2b | 88 | 40 | 0.0000 |
| adv-hybrid-expand-s2b | closed_book | 139 | 45 | 0.0000 |
| adv-hybrid-expand-s2b | naive-std | 169 | 32 | 0.0000 |
| adv-hybrid-expand-s2b | naive_rag | 280 | 13 | 0.0000 |
| adv-hybrid-expand-s2b | oracle | 10 | 100 | 0.0000 |
| adv-hybrid-expand-s2b | pipeshub | 13 | 98 | 0.0000 |
| adv-hybrid-rerank | adv-sparse | 76 | 13 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid | 31 | 107 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-decompose-rerank-s2b | 24 | 161 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-expand-rerank | 21 | 150 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-expand-rerank-s2b | 18 | 173 | 0.0000 |
| adv-hybrid-rerank | closed_book | 85 | 194 | 0.0000 |
| adv-hybrid-rerank | naive-std | 40 | 106 | 0.0000 |
| adv-hybrid-rerank | naive_rag | 88 | 24 | 0.0000 |
| adv-hybrid-rerank | oracle | 5 | 298 | 0.0000 |
| adv-hybrid-rerank | pipeshub | 7 | 295 | 0.0000 |
| adv-sparse | adv-std-hybrid | 21 | 160 | 0.0000 |
| adv-sparse | adv-std-hybrid-decompose-rerank-s2b | 17 | 217 | 0.0000 |
| adv-sparse | adv-std-hybrid-expand-rerank | 13 | 205 | 0.0000 |
| adv-sparse | adv-std-hybrid-expand-rerank-s2b | 10 | 228 | 0.0000 |
| adv-sparse | closed_book | 60 | 232 | 0.0000 |
| adv-sparse | naive-std | 34 | 163 | 0.0000 |
| adv-sparse | naive_rag | 76 | 75 | 1.0000 |
| adv-sparse | oracle | 3 | 359 | 0.0000 |
| adv-sparse | pipeshub | 3 | 354 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-decompose-rerank-s2b | 31 | 92 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-expand-rerank | 32 | 85 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-expand-rerank-s2b | 31 | 110 | 0.0000 |
| adv-std-hybrid | closed_book | 102 | 135 | 0.0374 |
| adv-std-hybrid | naive-std | 52 | 42 | 0.3533 |
| adv-std-hybrid | naive_rag | 162 | 22 | 0.0000 |
| adv-std-hybrid | oracle | 6 | 223 | 0.0000 |
| adv-std-hybrid | pipeshub | 5 | 217 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 52 | 44 | 0.4752 |
| adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank-s2b | 29 | 47 | 0.0505 |
| adv-std-hybrid-decompose-rerank-s2b | closed_book | 127 | 99 | 0.0723 |
| adv-std-hybrid-decompose-rerank-s2b | naive-std | 111 | 40 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | naive_rag | 219 | 18 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | oracle | 7 | 163 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | pipeshub | 13 | 164 | 0.0000 |
| adv-std-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | 26 | 52 | 0.0043 |
| adv-std-hybrid-expand-rerank | closed_book | 130 | 110 | 0.2200 |
| adv-std-hybrid-expand-rerank | naive-std | 100 | 37 | 0.0000 |
| adv-std-hybrid-expand-rerank | naive_rag | 216 | 23 | 0.0000 |
| adv-std-hybrid-expand-rerank | oracle | 6 | 170 | 0.0000 |
| adv-std-hybrid-expand-rerank | pipeshub | 9 | 168 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | closed_book | 130 | 84 | 0.0020 |
| adv-std-hybrid-expand-rerank-s2b | naive-std | 125 | 36 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | naive_rag | 236 | 17 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | oracle | 8 | 146 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | pipeshub | 8 | 141 | 0.0000 |
| closed_book | naive-std | 139 | 96 | 0.0060 |
| closed_book | naive_rag | 234 | 61 | 0.0000 |
| closed_book | oracle | 18 | 202 | 0.0000 |
| closed_book | pipeshub | 14 | 193 | 0.0000 |
| naive-std | naive_rag | 155 | 25 | 0.0000 |
| naive-std | oracle | 6 | 233 | 0.0000 |
| naive-std | pipeshub | 6 | 228 | 0.0000 |
| naive_rag | oracle | 3 | 360 | 0.0000 |
| naive_rag | pipeshub | 6 | 358 | 0.0000 |
| oracle | pipeshub | 22 | 17 | 0.5224 |

### Paired comparisons on grounded correctness (exact McNemar)

| A | B | A only | B only | p |
|---|---|---|---|---|
| adv-dense-decompose | adv-dense-expand | 38 | 135 | 0.0000 |
| adv-dense-decompose | adv-hybrid | 106 | 54 | 0.0000 |
| adv-dense-decompose | adv-hybrid-decompose | 39 | 70 | 0.0039 |
| adv-dense-decompose | adv-hybrid-decompose-rerank | 60 | 72 | 0.3384 |
| adv-dense-decompose | adv-hybrid-decompose-rerank-s2b | 46 | 110 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand | 24 | 162 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-rerank | 54 | 119 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-rerank-bge | 25 | 175 | 0.0000 |
| adv-dense-decompose | adv-hybrid-expand-s2b | 15 | 191 | 0.0000 |
| adv-dense-decompose | adv-hybrid-rerank | 99 | 65 | 0.0098 |
| adv-dense-decompose | adv-sparse | 142 | 45 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid | 64 | 116 | 0.0001 |
| adv-dense-decompose | adv-std-hybrid-decompose-rerank-s2b | 44 | 144 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid-expand-rerank | 52 | 143 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid-expand-rerank-s2b | 43 | 162 | 0.0000 |
| adv-dense-decompose | naive-std | 76 | 102 | 0.0606 |
| adv-dense-decompose | naive_rag | 136 | 26 | 0.0000 |
| adv-dense-decompose | oracle | 8 | 289 | 0.0000 |
| adv-dense-decompose | pipeshub | 14 | 260 | 0.0000 |
| adv-dense-expand | adv-hybrid | 188 | 39 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose | 121 | 55 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose-rerank | 138 | 53 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose-rerank-s2b | 114 | 81 | 0.0217 |
| adv-dense-expand | adv-hybrid-expand | 43 | 84 | 0.0003 |
| adv-dense-expand | adv-hybrid-expand-rerank | 98 | 66 | 0.0152 |
| adv-dense-expand | adv-hybrid-expand-rerank-bge | 41 | 94 | 0.0000 |
| adv-dense-expand | adv-hybrid-expand-s2b | 23 | 102 | 0.0000 |
| adv-dense-expand | adv-hybrid-rerank | 174 | 43 | 0.0000 |
| adv-dense-expand | adv-sparse | 227 | 33 | 0.0000 |
| adv-dense-expand | adv-std-hybrid | 124 | 79 | 0.0019 |
| adv-dense-expand | adv-std-hybrid-decompose-rerank-s2b | 92 | 95 | 0.8838 |
| adv-dense-expand | adv-std-hybrid-expand-rerank | 99 | 93 | 0.7183 |
| adv-dense-expand | adv-std-hybrid-expand-rerank-s2b | 87 | 109 | 0.1334 |
| adv-dense-expand | naive-std | 132 | 61 | 0.0000 |
| adv-dense-expand | naive_rag | 226 | 19 | 0.0000 |
| adv-dense-expand | oracle | 9 | 193 | 0.0000 |
| adv-dense-expand | pipeshub | 26 | 175 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose | 24 | 107 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose-rerank | 29 | 93 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose-rerank-s2b | 26 | 142 | 0.0000 |
| adv-hybrid | adv-hybrid-expand | 14 | 204 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-rerank | 31 | 148 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-rerank-bge | 16 | 218 | 0.0000 |
| adv-hybrid | adv-hybrid-expand-s2b | 10 | 238 | 0.0000 |
| adv-hybrid | adv-hybrid-rerank | 28 | 46 | 0.0474 |
| adv-hybrid | adv-sparse | 71 | 26 | 0.0000 |
| adv-hybrid | adv-std-hybrid | 23 | 127 | 0.0000 |
| adv-hybrid | adv-std-hybrid-decompose-rerank-s2b | 23 | 175 | 0.0000 |
| adv-hybrid | adv-std-hybrid-expand-rerank | 25 | 168 | 0.0000 |
| adv-hybrid | adv-std-hybrid-expand-rerank-s2b | 21 | 192 | 0.0000 |
| adv-hybrid | naive-std | 42 | 120 | 0.0000 |
| adv-hybrid | naive_rag | 86 | 28 | 0.0000 |
| adv-hybrid | oracle | 5 | 338 | 0.0000 |
| adv-hybrid | pipeshub | 13 | 311 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-decompose-rerank | 67 | 48 | 0.0928 |
| adv-hybrid-decompose | adv-hybrid-decompose-rerank-s2b | 52 | 85 | 0.0061 |
| adv-hybrid-decompose | adv-hybrid-expand | 30 | 137 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-expand-rerank | 65 | 99 | 0.0098 |
| adv-hybrid-decompose | adv-hybrid-expand-rerank-bge | 27 | 146 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-expand-s2b | 19 | 164 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-rerank | 102 | 37 | 0.0000 |
| adv-hybrid-decompose | adv-sparse | 151 | 23 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid | 66 | 87 | 0.1056 |
| adv-hybrid-decompose | adv-std-hybrid-decompose-rerank-s2b | 50 | 119 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid-expand-rerank | 58 | 118 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid-expand-rerank-s2b | 48 | 136 | 0.0000 |
| adv-hybrid-decompose | naive-std | 91 | 86 | 0.7638 |
| adv-hybrid-decompose | naive_rag | 158 | 17 | 0.0000 |
| adv-hybrid-decompose | oracle | 7 | 257 | 0.0000 |
| adv-hybrid-decompose | pipeshub | 22 | 237 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-decompose-rerank-s2b | 26 | 78 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand | 32 | 158 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-rerank | 33 | 86 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-rerank-bge | 24 | 162 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-expand-s2b | 17 | 181 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-hybrid-rerank | 77 | 31 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-sparse | 132 | 23 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid | 51 | 91 | 0.0010 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-decompose-rerank-s2b | 32 | 120 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-expand-rerank | 40 | 119 | 0.0000 |
| adv-hybrid-decompose-rerank | adv-std-hybrid-expand-rerank-s2b | 35 | 142 | 0.0000 |
| adv-hybrid-decompose-rerank | naive-std | 73 | 87 | 0.3041 |
| adv-hybrid-decompose-rerank | naive_rag | 144 | 22 | 0.0000 |
| adv-hybrid-decompose-rerank | oracle | 6 | 275 | 0.0000 |
| adv-hybrid-decompose-rerank | pipeshub | 21 | 255 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand | 55 | 129 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank | 69 | 70 | 1.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-rerank-bge | 47 | 133 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-expand-s2b | 26 | 138 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-hybrid-rerank | 122 | 24 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-sparse | 186 | 25 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid | 78 | 66 | 0.3594 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-decompose-rerank-s2b | 39 | 75 | 0.0010 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 54 | 81 | 0.0249 |
| adv-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank-s2b | 38 | 93 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | naive-std | 93 | 55 | 0.0022 |
| adv-hybrid-decompose-rerank-s2b | naive_rag | 192 | 18 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | oracle | 9 | 226 | 0.0000 |
| adv-hybrid-decompose-rerank-s2b | pipeshub | 27 | 209 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-expand-rerank | 112 | 39 | 0.0000 |
| adv-hybrid-expand | adv-hybrid-expand-rerank-bge | 46 | 58 | 0.2807 |
| adv-hybrid-expand | adv-hybrid-expand-s2b | 27 | 65 | 0.0001 |
| adv-hybrid-expand | adv-hybrid-rerank | 196 | 24 | 0.0000 |
| adv-hybrid-expand | adv-sparse | 248 | 13 | 0.0000 |
| adv-hybrid-expand | adv-std-hybrid | 137 | 51 | 0.0000 |
| adv-hybrid-expand | adv-std-hybrid-decompose-rerank-s2b | 107 | 69 | 0.0051 |
| adv-hybrid-expand | adv-std-hybrid-expand-rerank | 115 | 68 | 0.0006 |
| adv-hybrid-expand | adv-std-hybrid-expand-rerank-s2b | 96 | 77 | 0.1710 |
| adv-hybrid-expand | naive-std | 158 | 46 | 0.0000 |
| adv-hybrid-expand | naive_rag | 261 | 13 | 0.0000 |
| adv-hybrid-expand | oracle | 13 | 156 | 0.0000 |
| adv-hybrid-expand | pipeshub | 25 | 133 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-expand-rerank-bge | 26 | 111 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-expand-s2b | 26 | 137 | 0.0000 |
| adv-hybrid-expand-rerank | adv-hybrid-rerank | 124 | 25 | 0.0000 |
| adv-hybrid-expand-rerank | adv-sparse | 183 | 21 | 0.0000 |
| adv-hybrid-expand-rerank | adv-std-hybrid | 87 | 74 | 0.3443 |
| adv-hybrid-expand-rerank | adv-std-hybrid-decompose-rerank-s2b | 64 | 99 | 0.0076 |
| adv-hybrid-expand-rerank | adv-std-hybrid-expand-rerank | 65 | 91 | 0.0450 |
| adv-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | 57 | 111 | 0.0000 |
| adv-hybrid-expand-rerank | naive-std | 106 | 67 | 0.0037 |
| adv-hybrid-expand-rerank | naive_rag | 197 | 22 | 0.0000 |
| adv-hybrid-expand-rerank | oracle | 6 | 222 | 0.0000 |
| adv-hybrid-expand-rerank | pipeshub | 23 | 204 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-hybrid-expand-s2b | 40 | 66 | 0.0148 |
| adv-hybrid-expand-rerank-bge | adv-hybrid-rerank | 201 | 17 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-sparse | 257 | 10 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid | 141 | 43 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-decompose-rerank-s2b | 112 | 62 | 0.0002 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-expand-rerank | 118 | 59 | 0.0000 |
| adv-hybrid-expand-rerank-bge | adv-std-hybrid-expand-rerank-s2b | 95 | 64 | 0.0171 |
| adv-hybrid-expand-rerank-bge | naive-std | 167 | 43 | 0.0000 |
| adv-hybrid-expand-rerank-bge | naive_rag | 275 | 15 | 0.0000 |
| adv-hybrid-expand-rerank-bge | oracle | 17 | 148 | 0.0000 |
| adv-hybrid-expand-rerank-bge | pipeshub | 32 | 128 | 0.0000 |
| adv-hybrid-expand-s2b | adv-hybrid-rerank | 228 | 18 | 0.0000 |
| adv-hybrid-expand-s2b | adv-sparse | 283 | 10 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid | 157 | 33 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-decompose-rerank-s2b | 119 | 43 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank | 128 | 43 | 0.0000 |
| adv-hybrid-expand-s2b | adv-std-hybrid-expand-rerank-s2b | 105 | 48 | 0.0000 |
| adv-hybrid-expand-s2b | naive-std | 180 | 30 | 0.0000 |
| adv-hybrid-expand-s2b | naive_rag | 298 | 12 | 0.0000 |
| adv-hybrid-expand-s2b | oracle | 15 | 120 | 0.0000 |
| adv-hybrid-expand-s2b | pipeshub | 34 | 104 | 0.0000 |
| adv-hybrid-rerank | adv-sparse | 79 | 16 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid | 28 | 114 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-decompose-rerank-s2b | 23 | 157 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-expand-rerank | 23 | 148 | 0.0000 |
| adv-hybrid-rerank | adv-std-hybrid-expand-rerank-s2b | 21 | 174 | 0.0000 |
| adv-hybrid-rerank | naive-std | 41 | 101 | 0.0000 |
| adv-hybrid-rerank | naive_rag | 99 | 23 | 0.0000 |
| adv-hybrid-rerank | oracle | 6 | 321 | 0.0000 |
| adv-hybrid-rerank | pipeshub | 17 | 297 | 0.0000 |
| adv-sparse | adv-std-hybrid | 18 | 167 | 0.0000 |
| adv-sparse | adv-std-hybrid-decompose-rerank-s2b | 17 | 214 | 0.0000 |
| adv-sparse | adv-std-hybrid-expand-rerank | 14 | 202 | 0.0000 |
| adv-sparse | adv-std-hybrid-expand-rerank-s2b | 14 | 230 | 0.0000 |
| adv-sparse | naive-std | 34 | 157 | 0.0000 |
| adv-sparse | naive_rag | 77 | 64 | 0.3122 |
| adv-sparse | oracle | 3 | 381 | 0.0000 |
| adv-sparse | pipeshub | 10 | 353 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-decompose-rerank-s2b | 38 | 86 | 0.0000 |
| adv-std-hybrid | adv-std-hybrid-expand-rerank | 42 | 81 | 0.0006 |
| adv-std-hybrid | adv-std-hybrid-expand-rerank-s2b | 37 | 104 | 0.0000 |
| adv-std-hybrid | naive-std | 68 | 42 | 0.0167 |
| adv-std-hybrid | naive_rag | 182 | 20 | 0.0000 |
| adv-std-hybrid | oracle | 9 | 238 | 0.0000 |
| adv-std-hybrid | pipeshub | 21 | 215 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank | 56 | 47 | 0.4307 |
| adv-std-hybrid-decompose-rerank-s2b | adv-std-hybrid-expand-rerank-s2b | 32 | 51 | 0.0475 |
| adv-std-hybrid-decompose-rerank-s2b | naive-std | 115 | 41 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | naive_rag | 224 | 14 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | oracle | 10 | 191 | 0.0000 |
| adv-std-hybrid-decompose-rerank-s2b | pipeshub | 34 | 180 | 0.0000 |
| adv-std-hybrid-expand-rerank | adv-std-hybrid-expand-rerank-s2b | 31 | 59 | 0.0042 |
| adv-std-hybrid-expand-rerank | naive-std | 106 | 41 | 0.0000 |
| adv-std-hybrid-expand-rerank | naive_rag | 221 | 20 | 0.0000 |
| adv-std-hybrid-expand-rerank | oracle | 10 | 200 | 0.0000 |
| adv-std-hybrid-expand-rerank | pipeshub | 28 | 183 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | naive-std | 131 | 38 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | naive_rag | 247 | 18 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | oracle | 13 | 175 | 0.0000 |
| adv-std-hybrid-expand-rerank-s2b | pipeshub | 30 | 157 | 0.0000 |
| naive-std | naive_rag | 156 | 20 | 0.0000 |
| naive-std | oracle | 8 | 263 | 0.0000 |
| naive-std | pipeshub | 22 | 242 | 0.0000 |
| naive_rag | oracle | 3 | 394 | 0.0000 |
| naive_rag | pipeshub | 10 | 366 | 0.0000 |
| oracle | pipeshub | 58 | 23 | 0.0001 |

Judge agreement (Cohen's κ, primary vs secondary): adv-dense-decompose: 0.978, adv-dense-expand: 0.963, adv-hybrid: 0.954, adv-hybrid-decompose: 0.968, adv-hybrid-decompose-rerank: 0.974, adv-hybrid-decompose-rerank-s2b: 0.980, adv-hybrid-expand: 0.968, adv-hybrid-expand-rerank: 0.979, adv-hybrid-expand-rerank-bge: 0.958, adv-hybrid-expand-s2b: 0.972, adv-hybrid-rerank: 0.981, adv-sparse: 0.965, adv-std-hybrid: 0.964, adv-std-hybrid-decompose-rerank-s2b: 0.967, adv-std-hybrid-expand-rerank: 0.957, adv-std-hybrid-expand-rerank-s2b: 0.965, closed_book: 0.993, naive-std: 0.964, naive_rag: 0.958, oracle: 0.919, pipeshub: 0.989

