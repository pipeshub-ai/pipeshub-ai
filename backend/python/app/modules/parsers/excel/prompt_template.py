from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field, field_validator

# Prompt for summarizing an entire sheet with multiple tables
sheet_summary_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are an expert data analyst. Provide a concise summary of all tables in this Excel sheet, "
            "focusing on their relationships and overall purpose.",
        ),
        (
            "user",
            "Sheet name: {sheet_name}\n\nTables:\n{tables_data}\n\n"
            "Provide a comprehensive summary of all tables in this sheet.",
        ),
    ]
)

# Prompt for summarizing a single table
table_summary_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are an expert data analyst.",
        ),
        (
            "user",
            "Table headers: {headers}\n\nSample data:\n{sample_data}\n\n"
            "Provide a clear summary of this table's purpose and content.",
        ),
    ]
)

# A model can run a field on until its output limit: an integer tens of thousands of digits
# long has been seen in ``header_row_count``. Its range goes out in the schema as
# minimum/maximum, which Gemini, OpenAI and Anthropic structured output all accept, and is
# enforced here leniently, so one bad field never costs the whole answer. Strings are bounded
# by the callers' output-token cap, not here: strict OpenAI schemas do not document maxLength,
# and cutting a long row description would lose real content.
MAX_HEADER_ROW_COUNT = 1


def _header_row_count(value: Any) -> Any:  # noqa: ANN401
    """0 or 1. Any other answer reads as 0 (no header row), as the callers already read it.

    A string is converted only when it is a few digits long: ``int()`` of a model's string
    is never unbounded.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        value = int(text) if text.isascii() and text.isdigit() and len(text) <= 3 else 0
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or not 0 <= value <= MAX_HEADER_ROW_COUNT:
        return 0
    return value


class KeyedRowDescription(BaseModel):
    """One description, addressed by the opaque key the row carried in the prompt."""

    key: str
    description: str = ""


def _keep_well_formed_descriptions(value: Any) -> Any:  # noqa: ANN401
    """Drop entries that carry no usable key or no text, so one bad entry cannot fail a whole answer.

    A bare string (an older positional shape), an entry without a ``key`` or whose key is not a
    string or integer, and an entry whose description is not text (an object or a list) are
    dropped: the row it was meant for is simply unanswered and the caller handles it alone.
    Anything that is not a list is left for validation to reject.
    """
    if not isinstance(value, list):
        return value
    kept = []
    for item in value:
        if isinstance(item, KeyedRowDescription):
            kept.append(item)
            continue
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        description = item.get("description")
        if isinstance(key, bool) or not isinstance(key, (str, int)) or not str(key).strip():
            continue
        if not (description is None or isinstance(description, str)):
            continue
        kept.append({"key": str(key), "description": description or ""})
    return kept


class KeyedRowDescriptions(BaseModel):
    """Row descriptions keyed by row key (the answer to ``keyed_row_text_prompt``)."""

    descriptions: list[KeyedRowDescription] = Field(default_factory=list)

    _tolerant = field_validator("descriptions", mode="before")(_keep_well_formed_descriptions)


class TableEnrichment(BaseModel):
    """Summary, headers and row descriptions of a table's first batch, one call."""

    summary: str = ""
    headers: list[str] = Field(default_factory=list)
    header_row_count: int = Field(default=0, ge=0, le=MAX_HEADER_ROW_COUNT)
    descriptions: list[KeyedRowDescription] = Field(default_factory=list)

    _tolerant = field_validator("descriptions", mode="before")(_keep_well_formed_descriptions)
    _header_rows = field_validator("header_row_count", mode="before")(_header_row_count)


class TableSummaryHeaders(BaseModel):
    """Summary and headers alone, asked for when the combined call did not give them."""

    summary: str = ""
    headers: list[str] = Field(default_factory=list)
    header_row_count: int = Field(default=0, ge=0, le=MAX_HEADER_ROW_COUNT)

    _header_rows = field_validator("header_row_count", mode="before")(_header_row_count)


class TableHeaders(BaseModel):
    headers: list[str]


class HeaderDetection(BaseModel):
    has_headers: bool
    confidence: str  # "high" or "low"
    reasoning: str


class ExcelHeaderDetection(BaseModel):
    has_headers: bool
    num_header_rows: int  # 0 if no headers, 1, 2, 3+ for multi-row
    confidence: str  # "high" or "low"
    reasoning: str


class CSVHeaderDetection(BaseModel):
    has_headers: bool
    num_header_rows: int  # 0 if no headers, 1, 2, 3+ for multi-row
    confidence: str  # "high" or "low"
    reasoning: str


class TableHeaderAnalysis(BaseModel):
    """Merged header detection + optional header generation + table summary."""

    has_headers: bool
    num_header_rows: int  # 0 if no headers, 1, 2, 3+ for multi-row
    headers: list[str] = Field(default_factory=list)
    summary: str = ""
    confidence: str = "low"  # "high" or "low"
    reasoning: str = ""


# Summary, headers and row descriptions in a single call. Each row carries an opaque key the
# model copies into its answer, so a description is matched to its row by copied text and
# never by counting: a skipped, merged or reordered row can only lose its own description.
table_enrichment_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are a data analysis expert who analyses tables and converts structured data into natural language descriptions.

RULES:
- Every row is listed with a short key in square brackets. Describe each row under its own key: copy the key exactly as written.
- Write one description per row. Do NOT combine rows and do NOT split a row.
- Do NOT put keys, row numbers or phrases like "Row 1:" inside the description text.
- Each description should focus ONLY on the data content, not the row position.
""",
        ),
        (
            "user",
            """Analyse the following table.

Table Data ({row_count} rows; the number before a key only tells you which row is first):
{keyed_rows_data}
{header_context}
Produce a single JSON object with these fields:

1. "summary": describing this table's purpose and content.

2. "header_row_count": 1 if Row 1 is a header row, otherwise 0.

3. "headers": one name per column. If header_row_count is 1, use Row 1's values. Otherwise invent clear, concise, unique names from each column's content.

4. "descriptions": a list of {{"key": "<key>", "description": <text>}}, one entry for each data row, with the key copied from that row. Do not describe a header row.

Example input row:
Row 7 [K3QX]: {{"Name": "John Doe", "Age": 30, "Salary": 50000}}

Example correct entry (NO key in the text):
{{"key": "K3QX", "description": "John Doe is 30 years old with a salary of $50,000"}}

Example INCORRECT entry (contains the key in the text - DO NOT DO THIS):
{{"key": "K3QX", "description": "[K3QX] John Doe is 30 years old with a salary of $50,000"}}

Respond with ONLY a JSON object:
{{
    "summary": "Summary of the table",
    "header_row_count": 0,
    "headers": ["Header1", "Header2"],
    "descriptions": [
        {{"key": "<key of the first data row>", "description": "Description of that row"}},
        {{"key": "<key of the next data row>", "description": "Description of that row"}}
    ]
}}
""",
        ),
    ]
)

# Summary and headers alone: asked for when the combined call did not return them, so a
# table whose rows failed still keeps its summary and column names.
table_summary_headers_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a data analysis expert who analyses tables.",
        ),
        (
            "user",
            """Analyse the following table (the first {row_count} rows, numbered from 1).

{numbered_rows_data}
{header_context}
Produce a single JSON object with these fields:

1. "summary": describing this table's purpose and content.

2. "header_row_count": 1 if Row 1 is a header row, otherwise 0.

3. "headers": one name per column. If header_row_count is 1, use Row 1's values. Otherwise invent clear, concise, unique names from each column's content.

Respond with ONLY a JSON object:
{{
    "summary": "Summary of the table",
    "header_row_count": 0,
    "headers": ["Header1", "Header2"]
}}
""",
        ),
    ]
)

# Later batches (and the halves of a batch that failed): row descriptions only, by key.
keyed_row_text_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are a data analysis expert who converts structured data into natural language descriptions.
Your task is to convert each row of data into a clear, concise and detailed natural language description.
Use the provided table summary to make the descriptions more meaningful.

RULES:
- Every row is listed with a short key in square brackets. Describe each row under its own key: copy the key exactly as written.
- Write one description per row. Do NOT combine rows and do NOT split a row.
- Do NOT put keys, row numbers or phrases like "Row 1:" inside the description text.""",
        ),
        (
            "user",
            """Convert the following rows of data into natural language descriptions.

Table Summary:
{table_summary}

Rows Data ({row_count} rows, each with its key):
{keyed_rows_data}

Respond with ONLY a JSON object with one entry per row, with each key copied from its row:
{{
    "descriptions": [
        {{"key": "<key of the first row>", "description": "Description of that row"}},
        {{"key": "<key of the next row>", "description": "Description of that row"}}
    ]
}}""",
        ),
    ]
)

prompt = """
# Task:
You are a data analysis expert tasked with identifying and validating table headers in an Excel document. Your goal is to ensure each table has appropriate, descriptive headers that accurately represent the data columns.

# Input:
You will be given:
1. The current table being analyzed
2. Context of all tables in the sheet for reference
3. The table's position and metadata

# Analysis Guidelines:
1. Header Detection:
   - First, analyze if the first row contains valid headers
   - Check if headers are descriptive and meaningful
   - Verify headers match the data type and content below them
   - Ensure headers are unique within the table

2. Header Creation (if needed):
   - If headers are missing or inadequate, create appropriate ones
   - Base new headers on:
     * Column data content and patterns
     * Context from surrounding tables
     * Common business terminology
     * Standard naming conventions

3. Header Validation:
   - Ensure each header is:
     * Clear and concise
     * Descriptive of the column content
     * Professional and consistent in style
     * Free of special characters or spaces
     * Unique within the table

# Current Table:
{table_data}

# Context (Other Tables in Sheet):
{tables_context}

# Table Metadata:
- Start Position: Row {start_row}, Column {start_col}
- End Position: Row {end_row}, Column {end_col}
- Number of Columns: {num_columns}"""


# Prompt for generating headers from CSV data
excel_header_generation_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are a data analysis expert. Generate descriptive, professional column headers based on the data patterns you observe.

CRITICAL REQUIREMENT: You MUST return EXACTLY the number of headers specified. This is non-negotiable.
- If asked for N headers, return exactly N headers - no more, no less
- Count your headers before responding to ensure the count is correct
- Returning the wrong number of headers will cause system failures""",
        ),
        (
            "user",
            """Analyze the following sample rows from a CSV file (no headers present):

Sample Data (first {sample_count} rows):
{sample_data}

REQUIRED NUMBER OF COLUMNS: {column_count}

Your task: Generate EXACTLY {column_count} descriptive, professional column headers.

Guidelines:
1. Analyze the data type, patterns, and content of each column
2. Create clear, concise, and descriptive header names
3. Use professional terminology appropriate for the data domain
4. Ensure headers are unique
5. Avoid special characters; use underscores or camelCase if needed
6. Make headers specific enough to be meaningful but concise

VALIDATION CHECKLIST (verify before responding):
☐ Have you analyzed ALL {column_count} columns in the sample data?
☐ Have you created EXACTLY {column_count} header names?
☐ Have you counted the headers in your response to confirm it's {column_count}?

Example for 3 columns:
Sample: [["John", 30, "Engineer"], ["Jane", 25, "Designer"]]
Correct Response: {{"headers": ["Name", "Age", "Occupation"]}}  ← 3 headers for 3 columns
Wrong Response: {{"headers": ["Name", "Age"]}}  ← Only 2 headers (INVALID)
Wrong Response: {{"headers": ["Name", "Age", "Occupation", "Department"]}}  ← 4 headers (INVALID)

Respond with ONLY a JSON object with EXACTLY {column_count} headers:
{{
    "headers": ["Header1", "Header2", "Header3", ..., "Header{column_count}"]
}}

FINAL REMINDER: Your response must contain EXACTLY {column_count} headers. Count them before submitting.""",
        ),
    ]
)

# Prompt for detecting Excel headers and determining number of header rows
excel_header_detection_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a data analysis expert. Analyze Excel table rows to detect if headers exist and how many rows they span.",
        ),
        (
            "user",
            """Analyze these first rows of an Excel table:
{rows_text}

Determine:
1. Does this table have headers? (true/false)
2. If yes, how many rows do the headers span? (1, 2, 3, or more)

CRITICAL RULE for header detection:
- A row is a header row ONLY if ALL non-empty cells contain header-like values (category descriptors)
- If even ONE cell contains a data value (specific measurement, number, amount, identifier), the ENTIRE row is NOT a header
- You MUST check EVERY non-empty cell - do not stop after checking the first cell
- Empty cells can be ignored

COMMON MISTAKE to avoid:
- Row where first cell looks like a label but remaining cells are numeric values
- Example: ["Gross AOV", "$55.89", "$53.33", ...]
- This is NOT a header row - it's a DATA row with a row label in the first column
- The values $55.89, $53.33 are specific measurements, not category descriptors

CORE PRINCIPLE - Understanding Headers vs Data:

HEADER-LIKE values describe CATEGORIES or TYPES:
- They answer: "What TYPE/CATEGORY of information is in this column?"
- They are DESCRIPTIVE LABELS that remain constant across rows
- Examples: "Name", "Age", "Department", "Q1_Sales", "Region", "Month"
- Think: Would this value describe what kind of data belongs here?

DATA values are SPECIFIC INSTANCES or MEASUREMENTS:
- They answer: "What is the SPECIFIC VALUE for this entry?"
- They are ACTUAL MEASUREMENTS, OBSERVATIONS, or IDENTIFIERS
- They vary from row to row (not constant labels)
- Examples: "John Smith", 35, $55.89, 0.95, "jan-2024", "North", "ABC-123"
- Think: Is this a specific number, amount, measurement, name, or identifier?

KEY INSIGHT - Numbers and Measurements:
- ANY specific numeric value, regardless of format, is DATA
  * Plain numbers: 42, 1250.50, 0.95
  * Currency: $55.89, €100, £75, ¥1000 (any currency symbol + number)
  * Percentages: 95.5%, 42%, 0.93 (when representing a specific value)
  * Dates: 2024-01-15, Jan-2024, 15-Jan
- Numeric labels that describe columns could be headers: "Q1", "Q2", "2024", "Month1"
  (But these must appear with OTHER header-like values, not mixed with data)

Examples:

Example 1 - Valid single-row header:
Row 1: ["Name", "Age", "Department", "Salary"]
Row 2: ["John Smith", 35, "Sales", 75000]
→ Row 1 is a header (all cells are descriptive labels)
→ has_headers: true, num_header_rows: 1

Example 2 - Invalid header (contains data):
Row 1: ["Name", "John Smith", "Age", 35]
Row 2: ["Department", "Sales", "Salary", 75000]
→ Row 1 is NOT a header (contains data values: "John Smith" and 35)
→ Row 2 is NOT a header (contains data values: "Sales" as a specific value and 75000)
→ has_headers: false, num_header_rows: 0

Example 3 - Valid multi-row header:
Row 1: ["Sales", "Sales", "Expenses", "Expenses"]
Row 2: ["Q1", "Q2", "Q1", "Q2"]
Row 3: [1250, 1430, 890, 920]
→ Rows 1 and 2 are headers (all non-empty cells are descriptive labels)
→ Row 3 is data (contains numbers)
→ has_headers: true, num_header_rows: 2

Example 4 - Invalid header (row labels + data values):
Row 1: ["Gross AOV", "$55.89", "$53.33", "$58.22", "$55.44"]
Row 2: ["Net AOV", "$73.28", "$74.65", "$74.00", "$75.36"]
Row 3: ["New", 0.98, 0.95, 0.95, 0.93]
Row 4: ["Repeat", 0.93, 0.94, 0.94, 0.93]
→ Analysis: Row 1 has "Gross AOV" (descriptive) but $55.89, $53.33, etc. are SPECIFIC currency amounts (data)
→ All rows follow pattern: descriptive label in col 1, specific numeric values in cols 2+
→ This table has ROW LABELS in first column, not column headers in first row
→ has_headers: false, num_header_rows: 0

Consider:
- Single-row headers: One row with descriptive column names (ALL cells must be header-like)
- Multi-row headers: Headers spanning multiple rows (e.g., grouped categories in row 1, subcategories in row 2). Each header row must have ALL cells as header-like values
- No headers: All rows appear to be data, or any potential header row contains at least one data value

Excel-specific patterns to consider:
- Merged cells often indicate multi-row headers
- Bold/styled first rows often indicate headers
- Text in first row(s) + numbers/data in subsequent rows suggests headers
- BUT: Always apply the CRITICAL RULE - ALL non-empty cells must be header-like

Respond with a JSON object:
{{
    "has_headers": true or false,
    "num_header_rows": 0 or 1 or 2 or 3 (etc.),
    "confidence": "high" or "low",
    "reasoning": "Brief explanation of your decision"
}}""",
        ),
    ]
)


csv_header_generation_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a data analysis expert. Generate descriptive, professional column headers based on the data patterns you observe.",
        ),
        (
            "user",
            """Analyze the following sample rows from a CSV file (no headers present):

Sample Data (first {sample_count} rows):
{sample_data}

Number of columns: {column_count}

Generate descriptive, professional column headers that accurately represent the data in each column.

Guidelines:
1. Analyze the data type, patterns, and content of each column
2. Create clear, concise, and descriptive header names
3. Use professional terminology appropriate for the data domain
4. Ensure headers are unique
5. Avoid special characters; use underscores or camelCase if needed
6. Make headers specific enough to be meaningful but concise

Return exactly {column_count} headers, one for each column, in order.

Respond with ONLY a JSON object:
{{
    "headers": ["Header1", "Header2", "Header3", ...]
}}

Ensure the number of headers matches {column_count} exactly.""",
        ),
    ]
)


# Header detection + optional generated headers + summary in one call.
# Detection rules are copied verbatim from excel_header_detection_prompt;
# the generation checklist is from excel_header_generation_prompt.
table_header_analysis_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a data analysis expert. Analyze table rows to detect if headers exist "
            "and how many rows they span, generate column names when needed, and summarize the table.",
        ),
        (
            "user",
            """Analyze these first rows of a table:
{rows_text}

Number of columns: {column_count}

Determine:
1. Does this table have headers? (true/false)
2. If yes, how many rows do the headers span? (1, 2, 3, or more)

CRITICAL RULE for header detection:
- A row is a header row ONLY if ALL non-empty cells contain header-like values (category descriptors)
- If even ONE cell contains a data value (specific measurement, number, amount, identifier), the ENTIRE row is NOT a header
- You MUST check EVERY non-empty cell - do not stop after checking the first cell
- Empty cells can be ignored

COMMON MISTAKE to avoid:
- Row where first cell looks like a label but remaining cells are numeric values
- Example: ["Gross AOV", "$55.89", "$53.33", ...]
- This is NOT a header row - it's a DATA row with a row label in the first column
- The values $55.89, $53.33 are specific measurements, not category descriptors

CORE PRINCIPLE - Understanding Headers vs Data:

HEADER-LIKE values describe CATEGORIES or TYPES:
- They answer: "What TYPE/CATEGORY of information is in this column?"
- They are DESCRIPTIVE LABELS that remain constant across rows
- Examples: "Name", "Age", "Department", "Q1_Sales", "Region", "Month"
- Think: Would this value describe what kind of data belongs here?

DATA values are SPECIFIC INSTANCES or MEASUREMENTS:
- They answer: "What is the SPECIFIC VALUE for this entry?"
- They are ACTUAL MEASUREMENTS, OBSERVATIONS, or IDENTIFIERS
- They vary from row to row (not constant labels)
- Examples: "John Smith", 35, $55.89, 0.95, "jan-2024", "North", "ABC-123"
- Think: Is this a specific number, amount, measurement, name, or identifier?

KEY INSIGHT - Numbers and Measurements:
- ANY specific numeric value, regardless of format, is DATA
  * Plain numbers: 42, 1250.50, 0.95
  * Currency: $55.89, €100, £75, ¥1000 (any currency symbol + number)
  * Percentages: 95.5%, 42%, 0.93 (when representing a specific value)
  * Dates: 2024-01-15, Jan-2024, 15-Jan
- Numeric labels that describe columns could be headers: "Q1", "Q2", "2024", "Month1"
  (But these must appear with OTHER header-like values, not mixed with data)

Examples:

Example 1 - Valid single-row header:
Row 1: ["Name", "Age", "Department", "Salary"]
Row 2: ["John Smith", 35, "Sales", 75000]
→ Row 1 is a header (all cells are descriptive labels)
→ has_headers: true, num_header_rows: 1

Example 2 - Invalid header (contains data):
Row 1: ["Name", "John Smith", "Age", 35]
Row 2: ["Department", "Sales", "Salary", 75000]
→ Row 1 is NOT a header (contains data values: "John Smith" and 35)
→ Row 2 is NOT a header (contains data values: "Sales" as a specific value and 75000)
→ has_headers: false, num_header_rows: 0

Example 3 - Valid multi-row header:
Row 1: ["Sales", "Sales", "Expenses", "Expenses"]
Row 2: ["Q1", "Q2", "Q1", "Q2"]
Row 3: [1250, 1430, 890, 920]
→ Rows 1 and 2 are headers (all non-empty cells are descriptive labels)
→ Row 3 is data (contains numbers)
→ has_headers: true, num_header_rows: 2

Example 4 - Invalid header (row labels + data values):
Row 1: ["Gross AOV", "$55.89", "$53.33", "$58.22", "$55.44"]
Row 2: ["Net AOV", "$73.28", "$74.65", "$74.00", "$75.36"]
Row 3: ["New", 0.98, 0.95, 0.95, 0.93]
Row 4: ["Repeat", 0.93, 0.94, 0.94, 0.93]
→ Analysis: Row 1 has "Gross AOV" (descriptive) but $55.89, $53.33, etc. are SPECIFIC currency amounts (data)
→ All rows follow pattern: descriptive label in col 1, specific numeric values in cols 2+
→ This table has ROW LABELS in first column, not column headers in first row
→ has_headers: false, num_header_rows: 0

Consider:
- Single-row headers: One row with descriptive column names (ALL cells must be header-like)
- Multi-row headers: Headers spanning multiple rows (e.g., grouped categories in row 1, subcategories in row 2). Each header row must have ALL cells as header-like values
- No headers: All rows appear to be data, or any potential header row contains at least one data value

Also produce:
3. "headers": EXACTLY {column_count} column names.
   - If has_headers is true and num_header_rows is 1: use Row 1's values (fill blanks with Column_N).
   - If has_headers is true and num_header_rows > 1: you may leave headers as an empty list;
     the caller concatenates multi-row headers in code.
   - If has_headers is false: invent clear, concise, unique names from the data.

   VALIDATION CHECKLIST when inventing headers:
   ☐ Have you analyzed ALL {column_count} columns in the sample data?
   ☐ Have you created EXACTLY {column_count} header names?
   ☐ Have you counted the headers in your response to confirm it's {column_count}?

4. "summary": 2-4 sentences describing this table's purpose and content (always required).

Respond with a JSON object:
{{
    "has_headers": true or false,
    "num_header_rows": 0 or 1 or 2 or 3 (etc.),
    "headers": ["Header1", "Header2", ..., "Header{column_count}"] or [],
    "summary": "Summary of the table",
    "confidence": "high" or "low",
    "reasoning": "Brief explanation of your decision"
}}""",
        ),
    ]
)
