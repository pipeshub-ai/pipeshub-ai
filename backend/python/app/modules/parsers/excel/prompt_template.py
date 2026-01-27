from typing import List

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

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

class RowDescriptions(BaseModel):
    descriptions: List[str]


class TableHeaders(BaseModel):
    headers: List[str]


class HeaderDetection(BaseModel):
    has_headers: bool
    confidence: str  # "high" or "low"
    reasoning: str


class ExcelHeaderDetection(BaseModel):
    has_headers: bool
    num_header_rows: int  # 0 if no headers, 1, 2, 3+ for multi-row
    confidence: str  # "high" or "low"
    reasoning: str


# Prompt for converting row data into natural language
row_text_prompt_for_csv = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are a data analysis expert who converts structured data into natural language descriptions.
Your task is to convert each row of data into a clear, concise and detailed natural language description.
Use the provided table summary to make the descriptions more meaningful.

CRITICAL RULES:
- You MUST generate EXACTLY one description for each row - no more, no less.
- Do NOT skip any rows.
- Do NOT combine multiple rows into one description.
- Do NOT split one row into multiple descriptions.
- Process rows in the exact order they are provided.
- Do NOT include row numbers (like "Row 1:", "Row 2 presents...", etc.) in your descriptions.
- Each description should focus ONLY on the data content, not the row position.
"""),
        (
            "user",
            """Convert the following rows of data into natural language descriptions.

Table Summary:
{table_summary}

Rows Data (Total: {row_count} rows):
{numbered_rows_data}

IMPORTANT:
- You must return EXACTLY {row_count} descriptions - one for each row, in the same order.
- Do NOT include "Row 1:", "Row 2 presents...", or any row numbers in the descriptions.
- Focus only on describing the data content itself.

Example input row:
{{"Name": "John Doe", "Age": 30, "Salary": 50000}}

Example correct output (NO row numbers):
"John Doe is 30 years old with a salary of $50,000"

Example INCORRECT output (contains row number - DO NOT DO THIS):
"Row 1: John Doe is 30 years old with a salary of $50,000"

Respond with ONLY a JSON object:
{{
    "descriptions": [
        "Description for row 1",
        "Description for row 2",
        ...
        "Description for row {row_count}"
    ]
}}

Verify your response contains exactly {row_count} descriptions before submitting.""",
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

# Prompt for detecting if CSV first row contains headers
csv_header_detection_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a data analysis expert. Analyze the first few rows of a CSV file to determine if the first row contains valid column headers or if it is data.",
        ),
        (
            "user",
            """Analyze these first rows of a CSV file:

Row 1: {row1}
Row 2: {row2}
Row 3: {row3}
Row 4: {row4}
Row 5: {row5}
Row 6: {row6}

Determine if Row 1 contains valid, descriptive column headers or if it is data.

Consider:
1. Are the values in Row 1 descriptive text (e.g., "Name", "Age", "City") or generic patterns (e.g., "Column1", "Unnamed: 0", "Field_1")?
2. Do the values in Row 1 differ significantly in type/pattern from the subsequent rows?
3. Are Row 1 values unique and meaningful, or do they look like data values?
4. Generic patterns to detect: "Column1", "Column2", "Unnamed: 0", "Field_1", "col1", etc.
5. With more sample rows, can you identify consistent data patterns that confirm whether Row 1 is a header or data?

Respond with a JSON object:
{{
    "has_headers": true/false,
    "confidence": "high" or "low",
    "reasoning": "Brief explanation of your decision"
}}""",
        ),
    ]
)

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

row_text_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are a data analysis expert who converts structured data into natural language descriptions.
Your task is to convert each row of data into a clear, concise and detailed natural language description.
Use the provided table summary to make the descriptions more meaningful."""),
        (
            "user",
            """Please convert these rows of data into natural language descriptions.

Table Summary:
{table_summary}

Rows Data:
{rows_data}

Respond with ONLY a JSON object with the following structure:
{{
    "descriptions": [
        "Description of first row",
        "Description of second row",
        "Description of third row"
    ]
}}

Number of descriptions should be equal to the number of rows in the data. Do not include any other text or explanation in your response - only the JSON object.""",
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
Row 1: {row1}
Row 2: {row2}
Row 3: {row3}
Row 4: {row4}

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