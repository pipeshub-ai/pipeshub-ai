## FRAMES benchmark — `20260929T183724Z-full-rag-625d2d4d`

Status: VALID

- Dataset `58d9fb6330f3`, split sha `fe4fe15c6965`, corpus `a89e6af19589`
- Snapshot 2024-10-15, git `5b6d47f0bbe5`
- Models: answerer=gpt-5.6-luna, judge_primary=claude-sonnet-5, judge_secondary=gemini-3.8-flash

### Board

| System | FRAMES acc % (95% CI) | Grounded acc % (95% CI) | Memory-suspect | Strict % | All gold in context % | Context recall % | Citation integrity % | ALCE recall % | Correct ∧ grounded % | p95 latency s | LLM calls / q | Input tok / q | Output tok / q | Cost / q | Cost / correct |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| oracle | 93.0 (91.1–94.7) | 90.0 (88.0–92.0) | 2 (0.2%) | 91.9 (89.9–93.7) | 99.8 (99.4–100.0) | 99.9 (99.8–100.0) | – | – | – | 6.3 | 1.0 | 23,167 | 234 | $0.0049 | $0.0053 |
| adv-hybrid-expand | 73.4 (70.4–76.5) | 67.5 (64.2–70.6) | 7 (0.8%) | 72.6 (69.5–75.5) | 75.1 (72.2–78.0) | 90.1 (88.8–91.4) | 82.5 | 38.1 | 10.6 | 25.4 | 2.0 | 5,431 | 675 | $0.0019 | $0.0026 |
| adv-dense-expand | 69.1 (65.8–72.2) | 61.3 (58.0–64.6) | 16 (1.9%) | 68.0 (64.7–71.1) | 65.5 (62.4–68.8) | 85.5 (84.0–87.0) | 91.6 | 37.3 | 9.2 | 25.4 | 2.0 | 3,933 | 680 | $0.0016 | $0.0023 |
| closed_book | 64.9 (61.7–68.1) | – | – | 64.1 (60.8–67.2) | 0.0 (0.0–0.0) | 0.0 (0.0–0.0) | – | – | – | 95.0 | 1.0 | 80 | 2,507 | $0.0030 | $0.0047 |
| adv-std-hybrid | 60.2 (56.9–63.5) | 54.6 (51.2–58.0) | 5 (0.6%) | 59.0 (55.6–62.4) | 64.9 (61.7–68.2) | 85.6 (84.1–87.1) | 100.0 | 34.7 | 7.0 | 14.8 | 1.0 | 19,560 | 501 | $0.0045 | $0.0075 |
| adv-hybrid-decompose | 57.8 (54.4–61.0) | 51.6 (48.2–55.0) | 11 (1.3%) | 56.3 (52.9–59.6) | 69.1 (66.0–72.2) | 87.5 (86.2–88.9) | 81.6 | 39.1 | 8.5 | 32.8 | 2.0 | 6,230 | 718 | $0.0021 | $0.0036 |
| naive-std | 57.6 (54.4–60.9) | 50.1 (46.7–53.5) | 15 (1.8%) | 56.2 (52.8–59.6) | 55.0 (51.6–58.4) | 79.5 (77.8–81.3) | 100.0 | 35.2 | 7.1 | 17.3 | 1.0 | 19,116 | 532 | $0.0045 | $0.0077 |
| adv-dense-decompose | 52.7 (49.3–56.1) | 46.0 (42.7–49.4) | 18 (2.2%) | 51.3 (47.9–54.7) | 59.2 (55.9–62.5) | 82.1 (80.5–83.7) | 89.7 | 36.4 | 5.7 | 34.8 | 2.0 | 4,518 | 801 | $0.0019 | $0.0035 |
| adv-hybrid | 43.3 (39.9–46.7) | 36.8 (33.5–40.0) | 9 (1.1%) | 42.1 (38.7–45.5) | 52.5 (49.0–55.9) | 79.0 (77.3–80.7) | 78.4 | 34.7 | 4.6 | 18.4 | 1.0 | 5,390 | 562 | $0.0018 | $0.0040 |
| adv-sparse | 37.9 (34.6–41.3) | 31.1 (27.9–34.2) | 13 (1.6%) | 36.7 (33.4–40.0) | 47.3 (43.9–50.7) | 75.3 (73.5–77.1) | 72.5 | 33.9 | 3.9 | 13.8 | 1.0 | 7,001 | 499 | $0.0020 | $0.0053 |
| naive_rag | 36.3 (33.0–39.6) | 27.7 (24.6–30.7) | 18 (2.2%) | 35.0 (31.7–38.2) | 36.3 (33.0–39.6) | 67.6 (65.7–69.6) | 87.5 | 34.8 | 3.3 | 17.9 | 1.0 | 3,666 | 576 | $0.0014 | $0.0039 |

### By split (95% CI)

| Split | Metric | adv-dense-decompose | adv-dense-expand | adv-hybrid | adv-hybrid-decompose | adv-hybrid-expand | adv-sparse | adv-std-hybrid | closed_book | naive-std | naive_rag | oracle |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dev | FRAMES acc % | 54.5 (47.5–61.5) | 70.5 (64.0–76.5) | 45.0 (38.0–52.0) | 62.5 (55.5–69.0) | 74.5 (68.5–80.5) | 43.5 (36.5–50.5) | 66.0 (59.0–72.5) | 69.0 (62.5–75.5) | 60.5 (53.5–67.0) | 37.5 (31.0–44.5) | 92.5 (88.5–96.0) |
| dev | Grounded acc % | 49.0 (42.0–56.0) | 63.5 (57.0–70.0) | 37.0 (30.5–44.0) | 56.5 (49.5–63.5) | 68.5 (62.0–75.0) | 36.0 (29.5–42.5) | 58.5 (51.5–65.5) | – | 53.0 (46.0–60.0) | 28.5 (22.5–35.0) | 90.0 (85.5–94.0) |
| dev | Memory-suspect % | 2.0 (0.5–4.0) | 2.0 (0.5–4.0) | 1.5 (0.0–3.5) | 1.0 (0.0–2.5) | 0.5 (0.0–1.5) | 1.0 (0.0–2.5) | 0.5 (0.0–1.5) | – | 2.0 (0.5–4.0) | 2.5 (0.5–5.0) | 0.5 (0.0–1.5) |
| heldout | FRAMES acc % | 52.1 (48.1–55.9) | 68.6 (65.1–72.1) | 42.8 (38.9–46.8) | 56.2 (52.4–60.1) | 73.1 (69.6–76.4) | 36.1 (32.4–39.9) | 58.3 (54.5–62.2) | 63.6 (59.8–67.5) | 56.7 (52.9–60.6) | 35.9 (32.2–39.7) | 93.1 (91.0–95.0) |
| heldout | Grounded acc % | 45.0 (41.2–48.9) | 60.6 (56.7–64.4) | 36.7 (33.0–40.5) | 50.0 (46.2–53.8) | 67.1 (63.3–70.7) | 29.5 (26.0–33.0) | 53.4 (49.5–57.4) | – | 49.2 (45.4–53.0) | 27.4 (23.9–30.9) | 90.1 (87.7–92.3) |
| heldout | Memory-suspect % | 2.2 (1.1–3.4) | 1.9 (1.0–3.0) | 1.0 (0.3–1.8) | 1.4 (0.6–2.4) | 1.0 (0.3–1.8) | 1.8 (0.8–2.9) | 0.6 (0.2–1.3) | – | 1.8 (0.8–2.9) | 2.1 (1.1–3.2) | 0.2 (0.0–0.5) |

### Accuracy by reasoning type

| Label | adv-dense-decompose | adv-dense-expand | adv-hybrid | adv-hybrid-decompose | adv-hybrid-expand | adv-sparse | adv-std-hybrid | closed_book | naive-std | naive_rag | oracle |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Multiple constraints | 49.7 | 69.4 | 42.1 | 56.5 | 72.5 | 38.8 | 57.6 | 64.3 | 55.9 | 34.2 | 92.9 |
| Numerical reasoning | 50.2 | 61.4 | 37.5 | 52.9 | 67.2 | 30.4 | 53.2 | 57.0 | 53.9 | 33.4 | 88.4 |
| Post processing | 48.6 | 63.6 | 40.2 | 54.2 | 68.2 | 34.6 | 57.0 | 55.1 | 51.4 | 38.3 | 90.7 |
| Tabular reasoning | 44.1 | 61.9 | 36.4 | 51.7 | 67.8 | 32.6 | 47.9 | 58.5 | 49.6 | 32.6 | 90.7 |
| Temporal reasoning | 45.0 | 65.1 | 36.0 | 50.4 | 70.9 | 30.6 | 56.8 | 60.4 | 54.7 | 28.8 | 91.4 |

### Accuracy by gold-article count

| Label | adv-dense-decompose | adv-dense-expand | adv-hybrid | adv-hybrid-decompose | adv-hybrid-expand | adv-sparse | adv-std-hybrid | closed_book | naive-std | naive_rag | oracle |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | 65.8 | 79.2 | 53.0 | 67.7 | 83.1 | 46.0 | 69.3 | 66.8 | 69.6 | 46.6 | 93.9 |
| 3 | 47.4 | 66.3 | 36.8 | 55.1 | 69.8 | 31.9 | 56.8 | 63.2 | 51.9 | 30.2 | 93.3 |
| 4 | 45.5 | 66.4 | 43.3 | 50.7 | 68.7 | 36.6 | 56.0 | 61.2 | 53.0 | 35.8 | 91.8 |
| 5+ | 34.8 | 46.7 | 30.4 | 42.4 | 58.7 | 30.4 | 45.7 | 69.6 | 41.3 | 20.7 | 90.2 |

### Evidence verification

Every answer the primary judge marked correct is checked against the context its system showed the answering model. *Memory-suspect*: judged correct, but the facts it depends on are not in that context. A system shown no context (closed book) lands every correct answer there.

| System | Supported | Partial | Memory-suspect | No evidence | Unparseable | Verified % |
|---|---|---|---|---|---|---|
| adv-dense-decompose | 379 | 37 | 18 | 0 | 0 | 100.0 |
| adv-dense-expand | 505 | 47 | 16 | 0 | 1 | 100.0 |
| adv-hybrid | 303 | 45 | 9 | 0 | 0 | 100.0 |
| adv-hybrid-decompose | 425 | 40 | 11 | 0 | 0 | 100.0 |
| adv-hybrid-expand | 556 | 42 | 7 | 0 | 0 | 100.0 |
| adv-sparse | 256 | 41 | 13 | 0 | 2 | 100.0 |
| adv-std-hybrid | 450 | 39 | 5 | 0 | 2 | 100.0 |
| naive-std | 413 | 45 | 15 | 0 | 2 | 100.0 |
| naive_rag | 228 | 53 | 18 | 0 | 0 | 100.0 |
| oracle | 742 | 19 | 2 | 0 | 3 | 100.0 |

Memory-suspect questions:

- **adv-dense-decompose**: 68, 71, 86, 158, 185, 230, 239, 310, 388, 415, 450, 540, 547, 618, 660, 688, 699, 708
- **adv-dense-expand**: 11, 43, 68, 82, 239, 240, 264, 310, 453, 645, 691, 702, 720, 728, 754, 803
- **adv-hybrid**: 37, 256, 296, 331, 410, 415, 688, 691, 699
- **adv-hybrid-decompose**: 27, 106, 172, 229, 239, 277, 383, 408, 447, 478, 728
- **adv-hybrid-expand**: 86, 106, 229, 419, 444, 645, 728
- **adv-sparse**: 88, 106, 115, 194, 229, 282, 336, 447, 540, 621, 688, 691, 701
- **adv-std-hybrid**: 9, 27, 51, 136, 388
- **naive-std**: 7, 27, 37, 68, 71, 161, 172, 256, 291, 438, 446, 530, 720, 728, 748
- **naive_rag**: 11, 37, 53, 68, 169, 359, 410, 443, 473, 530, 547, 688, 690, 691, 701, 702, 728, 769
- **oracle**: 27, 652

### Paired comparisons (exact McNemar)

| A | B | A only | B only | p |
|---|---|---|---|---|
| adv-dense-decompose | adv-dense-expand | 45 | 180 | 0.0000 |
| adv-dense-decompose | adv-hybrid | 143 | 66 | 0.0000 |
| adv-dense-decompose | adv-hybrid-decompose | 61 | 103 | 0.0013 |
| adv-dense-decompose | adv-hybrid-expand | 35 | 206 | 0.0000 |
| adv-dense-decompose | adv-sparse | 195 | 73 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid | 87 | 149 | 0.0001 |
| adv-dense-decompose | closed_book | 111 | 212 | 0.0000 |
| adv-dense-decompose | naive-std | 91 | 132 | 0.0073 |
| adv-dense-decompose | naive_rag | 168 | 33 | 0.0000 |
| adv-dense-decompose | oracle | 11 | 343 | 0.0000 |
| adv-dense-expand | adv-hybrid | 252 | 40 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose | 161 | 68 | 0.0000 |
| adv-dense-expand | adv-hybrid-expand | 49 | 85 | 0.0024 |
| adv-dense-expand | adv-sparse | 303 | 46 | 0.0000 |
| adv-dense-expand | adv-std-hybrid | 166 | 93 | 0.0000 |
| adv-dense-expand | closed_book | 136 | 102 | 0.0322 |
| adv-dense-expand | naive-std | 170 | 76 | 0.0000 |
| adv-dense-expand | naive_rag | 288 | 18 | 0.0000 |
| adv-dense-expand | oracle | 10 | 207 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose | 26 | 145 | 0.0000 |
| adv-hybrid | adv-hybrid-expand | 12 | 260 | 0.0000 |
| adv-hybrid | adv-sparse | 80 | 35 | 0.0000 |
| adv-hybrid | adv-std-hybrid | 33 | 172 | 0.0000 |
| adv-hybrid | closed_book | 96 | 274 | 0.0000 |
| adv-hybrid | naive-std | 50 | 168 | 0.0000 |
| adv-hybrid | naive_rag | 99 | 41 | 0.0000 |
| adv-hybrid | oracle | 4 | 413 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-expand | 38 | 167 | 0.0000 |
| adv-hybrid-decompose | adv-sparse | 193 | 29 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid | 86 | 106 | 0.1701 |
| adv-hybrid-decompose | closed_book | 135 | 194 | 0.0013 |
| adv-hybrid-decompose | naive-std | 109 | 108 | 1.0000 |
| adv-hybrid-decompose | naive_rag | 203 | 26 | 0.0000 |
| adv-hybrid-decompose | oracle | 9 | 299 | 0.0000 |
| adv-hybrid-expand | adv-sparse | 311 | 18 | 0.0000 |
| adv-hybrid-expand | adv-std-hybrid | 175 | 66 | 0.0000 |
| adv-hybrid-expand | closed_book | 154 | 84 | 0.0000 |
| adv-hybrid-expand | naive-std | 190 | 60 | 0.0000 |
| adv-hybrid-expand | naive_rag | 323 | 17 | 0.0000 |
| adv-hybrid-expand | oracle | 12 | 173 | 0.0000 |
| adv-sparse | adv-std-hybrid | 31 | 215 | 0.0000 |
| adv-sparse | closed_book | 88 | 311 | 0.0000 |
| adv-sparse | naive-std | 49 | 212 | 0.0000 |
| adv-sparse | naive_rag | 110 | 97 | 0.4043 |
| adv-sparse | oracle | 5 | 459 | 0.0000 |
| adv-std-hybrid | closed_book | 138 | 177 | 0.0321 |
| adv-std-hybrid | naive-std | 75 | 54 | 0.0778 |
| adv-std-hybrid | naive_rag | 227 | 30 | 0.0000 |
| adv-std-hybrid | oracle | 9 | 279 | 0.0000 |
| closed_book | naive-std | 193 | 133 | 0.0011 |
| closed_book | naive_rag | 319 | 83 | 0.0000 |
| closed_book | oracle | 26 | 257 | 0.0000 |
| naive-std | naive_rag | 207 | 31 | 0.0000 |
| naive-std | oracle | 9 | 300 | 0.0000 |
| naive_rag | oracle | 6 | 473 | 0.0000 |

### Paired comparisons on grounded correctness (exact McNemar)

| A | B | A only | B only | p |
|---|---|---|---|---|
| adv-dense-decompose | adv-dense-expand | 52 | 178 | 0.0000 |
| adv-dense-decompose | adv-hybrid | 145 | 69 | 0.0000 |
| adv-dense-decompose | adv-hybrid-decompose | 53 | 99 | 0.0002 |
| adv-dense-decompose | adv-hybrid-expand | 36 | 213 | 0.0000 |
| adv-dense-decompose | adv-sparse | 190 | 67 | 0.0000 |
| adv-dense-decompose | adv-std-hybrid | 85 | 156 | 0.0000 |
| adv-dense-decompose | naive-std | 100 | 134 | 0.0308 |
| adv-dense-decompose | naive_rag | 184 | 33 | 0.0000 |
| adv-dense-decompose | oracle | 14 | 377 | 0.0000 |
| adv-dense-expand | adv-hybrid | 248 | 46 | 0.0000 |
| adv-dense-expand | adv-hybrid-decompose | 154 | 74 | 0.0000 |
| adv-dense-expand | adv-hybrid-expand | 54 | 105 | 0.0001 |
| adv-dense-expand | adv-sparse | 293 | 44 | 0.0000 |
| adv-dense-expand | adv-std-hybrid | 161 | 106 | 0.0009 |
| adv-dense-expand | naive-std | 171 | 79 | 0.0000 |
| adv-dense-expand | naive_rag | 300 | 23 | 0.0000 |
| adv-dense-expand | oracle | 12 | 249 | 0.0000 |
| adv-hybrid | adv-hybrid-decompose | 27 | 149 | 0.0000 |
| adv-hybrid | adv-hybrid-expand | 17 | 270 | 0.0000 |
| adv-hybrid | adv-sparse | 84 | 37 | 0.0000 |
| adv-hybrid | adv-std-hybrid | 32 | 179 | 0.0000 |
| adv-hybrid | naive-std | 56 | 166 | 0.0000 |
| adv-hybrid | naive_rag | 115 | 40 | 0.0000 |
| adv-hybrid | oracle | 5 | 444 | 0.0000 |
| adv-hybrid-decompose | adv-hybrid-expand | 41 | 172 | 0.0000 |
| adv-hybrid-decompose | adv-sparse | 199 | 30 | 0.0000 |
| adv-hybrid-decompose | adv-std-hybrid | 89 | 114 | 0.0918 |
| adv-hybrid-decompose | naive-std | 123 | 111 | 0.4722 |
| adv-hybrid-decompose | naive_rag | 220 | 23 | 0.0000 |
| adv-hybrid-decompose | oracle | 10 | 327 | 0.0000 |
| adv-hybrid-expand | adv-sparse | 319 | 19 | 0.0000 |
| adv-hybrid-expand | adv-std-hybrid | 176 | 70 | 0.0000 |
| adv-hybrid-expand | naive-std | 207 | 64 | 0.0000 |
| adv-hybrid-expand | naive_rag | 346 | 18 | 0.0000 |
| adv-hybrid-expand | oracle | 15 | 201 | 0.0000 |
| adv-sparse | adv-std-hybrid | 28 | 222 | 0.0000 |
| adv-sparse | naive-std | 48 | 205 | 0.0000 |
| adv-sparse | naive_rag | 110 | 82 | 0.0511 |
| adv-sparse | oracle | 4 | 490 | 0.0000 |
| adv-std-hybrid | naive-std | 91 | 54 | 0.0027 |
| adv-std-hybrid | naive_rag | 251 | 29 | 0.0000 |
| adv-std-hybrid | oracle | 11 | 303 | 0.0000 |
| naive-std | naive_rag | 212 | 27 | 0.0000 |
| naive-std | oracle | 11 | 340 | 0.0000 |
| naive_rag | oracle | 5 | 519 | 0.0000 |

Judge agreement (Cohen's κ, primary vs secondary): adv-dense-decompose: 0.971, adv-dense-expand: 0.958, adv-hybrid: 0.955, adv-hybrid-decompose: 0.965, adv-hybrid-expand: 0.957, adv-sparse: 0.958, adv-std-hybrid: 0.962, closed_book: 0.984, naive-std: 0.965, naive_rag: 0.958, oracle: 0.930
