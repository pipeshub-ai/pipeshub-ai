# pptx / docx benchmark suite

Five representative prompts to compare the **before** and **after** quality of
the sandbox's document generation. Run each prompt through the agent with the
new document design skill wired in and rate the output on a 1–5 design scale.

## How to run

1. Send each prompt below to the pipeshub agent. Save the returned `.pptx` /
   `.docx` as `<prompt-id>.after.pptx` / `.docx`.
2. For the **before** baseline, temporarily disable the planner hint in
   `backend/python/app/modules/agents/qna/nodes.py` ("Document Generation
   Rule (.pptx / .docx)" section) and re-send the same prompts. Save as
   `<prompt-id>.before.pptx`.
3. Render both to thumbnails using `coding_sandbox.render_artifact_preview`
   or `soffice --headless --convert-to pdf ... && pdftoppm -jpeg -r 120 ...`.
4. Rate each output on the rubric below.

## Rating rubric (1–5)

| Score | Meaning                                                                 |
|-------|-------------------------------------------------------------------------|
| 5     | Professional. Coherent palette, typographic hierarchy, varied layouts, visuals on every slide, zero overflow. |
| 4     | Solid. Minor polish issues (one low-contrast element, one crowded slide) but nothing embarrassing. |
| 3     | OK. Readable and on-brand but uses the same layout repeatedly; visuals are sparse. |
| 2     | Rough. All text, no visual elements, default blue, repeated identical layout. |
| 1     | Broken. Overflow, overlap, unreadable contrast, or leftover placeholder text. |

Track the aggregate mean across the five prompts. Ship threshold: **after**
mean ≥ 4.0 and no individual score below 3.

## Prompts

See `prompts/*.md` for one file per benchmark. Each contains the exact user
message, any supporting data, and notes on what a great output looks like.

## Reference implementations

`examples/` contains hand-written TypeScript programs that produce what a
"great" output looks like for each prompt using the in-repo helper libraries.
Use these to debug the agent when it diverges.
