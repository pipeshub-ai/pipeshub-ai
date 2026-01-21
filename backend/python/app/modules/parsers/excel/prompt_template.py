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
