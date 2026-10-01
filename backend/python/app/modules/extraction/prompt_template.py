prompt_for_image_description = """
# Role
You are a precise document image-to-text specialist. Convert the provided image into clean, searchable text for enterprise document indexing.

# Core Instructions
1. **Extract all visible text** exactly as written—preserve spelling, punctuation, capitalization, numbers, and units verbatim
2. **Maintain reading order**: top-to-bottom, left-to-right (or the natural order for multi-column or diagram layouts)
3. **Preserve structure** using markdown where helpful: headings (`#`), lists (`-` / `1.`), **bold**, *italic*, and tables (`|` with `---` headers)

# Visual Elements
When the image contains non-text content, describe it in enough detail to be searchable:
- **Charts/graphs**: type, title, axis labels, legend, and key values or trends
- **Diagrams/flowcharts**: structure, flow direction, and labeled components or connections
- **Tables rendered as images**: transcribe cell contents row by row
- **Logos/branding**: company or product name if identifiable
- **Photos/illustrations**: subject, setting, and any visible labels or signage
- **UI screenshots**: app or page name, visible controls, and on-screen text

If the image is purely visual with no readable text, provide a thorough descriptive transcription instead of a one-line caption.

# Output
Return ONLY the extracted and converted text. No preamble, no explanations, no commentary.
"""

prompt_for_document_extraction = """
# Task:
You are processing a document of an individual or an enterprise. Your task is to classify the document departments, categories, subcategories, languages, and topics, and to write a summary that a retrieval system will use to decide whether this document is relevant to a user's query.
Instructions must be strictly followed.

# File Metadata:
File name: {record_name}
File type: {record_type}

Use the file metadata as supporting evidence — a name like "invoice_2024_ACME.pdf" or a type of "spreadsheet" can help disambiguate an otherwise ambiguous category or department. The document content is still the primary source of truth: never infer content, topics, or summary details from the filename alone.

# Analysis Guidelines:
1. **Departments**:
   - Choose **1 to 3 departments** ONLY from the provided list below.
   - Each department MUST **exactly match one** of the values in the list.
   - Any unlisted or paraphrased value is INVALID.
   - Use the following list:
     {department_list}

2. Document Type Categories & Subcategories:
   - `category`: Broad classification such as "Security", "Compliance", or "Technical Documentation".
   - `subcategories`:
     - `level1`: General sub-area under the main category.
     - `level2`: A more specific focus within level 1.
     - `level3`: The most detailed classification (if available).
   - Leave levels blank (`""`) if no further depth exists.
   - Do not provide comma-separated values for subcategories

   Example:
      Category: "Legal"
      Sub-category Level 1: "Contract"
      Sub-category Level 2: "Non Disclosure Agreement"
      Sub-category Level 3: "Confidentiality Agreement"

3. Languages:
   - List all languages found in the content
   - Use full ISO language names (e.g., "English", "French", "German").

4. **Topics**:
   - Extract the main themes and subjects discussed.
   - Be concise and avoid duplicates or near-duplicates.
   - Provide **3 to 6** unique, highly relevant topics.

5. **Summary**:
   - This is the single most important field. A retrieval system shows only the FIRST ~600 CHARACTERS of this summary to a model deciding whether to fetch the full document — everything after that point is a bonus, not a guarantee it will be read.
   - **Sentence 1 is load-bearing.** It must state, in this order: document type, primary subject, principal parties or owning team, and time period or effective date. It must stand alone as a complete relevance judgment, since a reader may see nothing else.
   - **Length is tiered to document depth, not fixed:**
     - Thin or sparse documents (a short note, a single form, a fragment): 3 to 5 sentences is enough. Do not pad.
     - Typical documents: aim for no more than 300 words.
     - Never exceed 400 words, regardless of document length.
   - **Density over prose.** After sentence 1, prioritize concrete, searchable facts over connective narration:
     - Named entities verbatim: people, organizations, teams, products, systems, projects, vendors, locations. Do not paraphrase proper nouns. When the document gives both an acronym and its expansion, keep both.
     - Concrete identifiers and figures: amounts with currency, dates, version numbers, ticket/contract/invoice numbers, percentages, quantities.
     - The specific questions this document can answer, and — when evident — the scope it does NOT cover.
   - Write in English; keep proper nouns in their original language and script.
   - **Forbidden:** opening filler ("This document provides...", "The purpose of this document is..."), meta commentary about the document or your own analysis, recommendations, opinions, and any claim not directly supported by the content shown.
   - If the content is too sparse or fragmentary to say anything substantive, write a short, honest summary of only what is verifiably present rather than inventing detail to hit a length target.

# Output:
Return a single structured object matching the required schema. Do not include any additional commentary outside the schema fields.
"""


prompt_for_code_extraction = """
# Task:
You are analysing one source file from an enterprise codebase and producing structured metadata for a code search index.

The individual symbols in this file (functions, classes, methods) are already indexed separately. Do NOT re-list them. Describe what the file does as a unit, what it talks to, and the words a developer would type when looking for it.

# File under analysis:
- Path: {file_path}
- Language: {language}

# Analysis Guidelines:

1. **Architecture Role**:
   - Pick exactly ONE value from the list below, copied verbatim.
   - Any unlisted or paraphrased value is INVALID.
{architecture_role_list}

2. **Category & Subcategories** (the feature/domain path this file belongs to):
   - `category`: the broad system area, e.g. "Indexing", "Connectors", "Authentication", "Search".
   - `subcategories.level1` / `level2`: narrower areas beneath it.
   - `subcategories.level3`: most specific, or "" when there is nothing more to say.
   - Anchor level1 and level2 on the directory structure in the file path above - two files in the
     same directory must land on the same level1. Use judgement only for level3.
   - Do not put comma-separated values in a single level.

   Example for "app/connectors/sources/microsoft/sharepoint/connector.py":
      Category: "Connectors"
      Sub-category Level 1: "Microsoft"
      Sub-category Level 2: "SharePoint"
      Sub-category Level 3: "Site Sync"

3. **Topics**:
   - 3 to 6 cross-cutting technical concepts, as lowercase noun phrases.
   - Good: "oauth2 token refresh", "exponential backoff", "idempotent consumer", "vector upsert".
   - Bad: symbol names from this file, the file name, the programming language.
   - These link this file to files elsewhere in the repository, so prefer concepts another file could share.

4. **Summary**:
   - Open with the file's path and what KIND of artifact it is, as one clause. Copy the path
     verbatim from "File under analysis" above. Choose the kind from: implementation, test suite,
     test fixtures, configuration, type definitions, script, documentation.

       "app/services/vector_db/vector_db_provider_factory.py — factory that ..."
       "tests/unit/parsers/test_html_parser_shim.py — test suite for ..."

     Without this, a test file is summarised in the vocabulary of the thing it tests and reads
     as that thing's implementation, so a search for the implementation returns the test.
   - Then 2 to 4 sentences: what the file is responsible for, which feature or workflow it serves,
     which external systems it touches, and the one or two entry points a caller would use.
   - Name the concrete identifiers this file owns — environment variables, configuration keys,
     class names, route paths, topic names. Those are the strings a developer actually types when
     searching, and they appear in few other files.
   - Do not enumerate every symbol, and do not describe the file's size or structure.

5. **Design Patterns**:
   - 0 to 3 patterns, only where the structure is genuinely present: factory, repository, strategy,
     decorator, dependency injection, observer, adapter, singleton.
   - Return an empty array when none is clear. Do not infer a pattern from a name alone.

6. **External Dependencies**:
   - The systems this file actually talks to, as concrete as the code allows.
   - Include third-party services and APIs, datastores, message topics, queues, cloud services.
   - Name specifics when visible: "Kafka topic RECORD_EVENTS", "Qdrant", "Microsoft Graph API", "Redis".
   - Exclude standard-library modules and imports of other modules from this same codebase.
   - Return an empty array when the file only touches internal code.

# Output Format:
You must return a single valid JSON object with the following structure:
{
    "architecture_role": string,  // exactly one value from the list above
    "category": string,  // broad system area
    "subcategories": {
        "level1": string,
        "level2": string,
        "level3": string
    },
    "topics": string[],  // 3 to 6 lowercase technical noun phrases
    "summary": string,  // 2 to 4 sentences for a developer searching the codebase
    "design_patterns": string[],  // 0 to 3, empty when none is clear
    "external_dependencies": string[]  // empty when the file only touches internal code
}

Return the JSON object only, no additional text or explanation.
"""
