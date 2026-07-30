

agent_block_group_prompt = """[{{label}} #{{block_group_index}}]{% for block in blocks %}
- {{block.content}}
{% endfor %}
"""

table_prompt = """[Table #{{block_group_index}}: {{ table_summary }}]{% for row in table_rows %}
[{{row.block_index}}|{{row.citation_ref}}] {{row.content}}
{% endfor %}
"""

block_group_prompt = """[{{label}} #{{block_group_index}}]{% for block in blocks %}
[{{block.block_index}}|{{block.citation_ref}}] {{block.content}}
{% endfor %}
"""

qna_prompt_context_header = """
  Context for Current Query:
"""



qna_prompt_context = """<record>
{% if context_metadata %}
{{ context_metadata }}
{% endif %}
"""


# Simple prompt for lightweight models (Ollama, small models)
qna_prompt_simple = """
Answer the user's query based on the context below.
<task>
You are an expert AI assistant within an enterprise who can answer any query based on the company's knowledge sources.
Records could be from multiple connector apps like Slack messages, emails, Google Drive files, etc.
Answer user queries based on the provided context (records), user information, and maintain a coherent conversational flow.
Ensure that document records only influence the current query and not subsequent unrelated follow-up query.
Relevant blocks of the records are provided in the context below.
</task>
<query>
Query: {{ query }}
</query>
<context>
{% for chunk in chunks %}
- Record Name: {{ chunk.metadata.recordName }}
- Citation ID: {{ chunk.metadata.citation_ref }}
- Block Content: {{ chunk.metadata.blockText }}
{% endfor %}
</context>
<instructions>
- Use only the provided context to answer the query.
- Cite key facts using the Citation ID as a markdown link: [source](Citation ID). Focus on important claims, not every sentence.
- Each block has a unique Citation ID like ref1, ref2. Use it exactly as shown.
- Limit to the most relevant citations. Do NOT cite every sentence.
- Place citations immediately after the relevant claim.
- Reuse the same link if citing the same block again.
- Do NOT number citations manually — just use [source](refN) format.
- If you cannot find the Citation ID for a fact, omit the citation rather than guessing.
</instructions>
Your answer: """


