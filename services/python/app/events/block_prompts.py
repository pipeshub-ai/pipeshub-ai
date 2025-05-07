block_extraction_prompt = """
You are an expert in enterprise document analysis and segmentation. Your task is to analyze the provided document blocks (from any enterprise domain—policies, procedures, reports, manuals, contracts, etc.) and divide them into coherent sections based solely on content. Use your domain reasoning to determine natural breaks between topics or logical divisions. Remember:
Each section must consist of contiguous blocks.
Every block should appear in exactly one section.
Provide clear and concise reasoning for each section you form.
Return your answer strictly in the JSON format provided below.
Instructions:
Contiguous Grouping
All blocks are numbered sequentially (e.g., 1, 2, 3 …).
Sections must be composed of contiguous blocks.
No block should be omitted or assigned to multiple sections.
Minimizing Section Size While Maintaining Completeness
Do not group an entire long section as one if it contains multiple independent parts.
Split large sections into smaller ones that remain complete and meaningful on their own.
Keep independent topics or clauses separate.
Avoid grouping multiple independent topics in a single section.
Logical Justification for Sections
Each section must include a justification explaining why those blocks belong together.
Justifications should mention topic continuity, introduction of new subtopics, or logical separation.
Output Format & Requirements
Include every block number in the output, ensuring no block is skipped.
In your output JSON, give a valid JSON object without unnecessary whitespace or extraneous delimiters.
Return your final answer as a JSON object exactly following this structure:
{
  "sections": [
    {
      "start_block": <number>,
      "end_block": <number>,
      "reason": "<brief explanation>"
      "confidence": ["Very High", "High", "Medium", "Low"]
    }
    …
  ]
}
Below is the document content:
 {{ formatted_document_blocks }}
Now, please provide the JSON with your identified sections.
"""


