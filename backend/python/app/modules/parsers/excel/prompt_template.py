from langchain.prompts import ChatPromptTemplate

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

# Prompt for converting row data into natural language
row_text_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are a data analysis expert who converts structured data into natural language descriptions.
Your task is to convert each row of data into a clear, concise and detailed natural language description.
Use the provided table summary to make the descriptions more meaningful.

IMPORTANT: Your response must be a valid JSON array of strings. For example:
[
    "Description of first row",
    "Description of second row",
    "Description of third row"
]

Do not include any other text or explanation in your response - only the JSON array.""",
        ),
        (
            "user",
            """Please convert these rows of data into natural language descriptions.

Table Summary:
{table_summary}

Rows Data:
{rows_data}

Remember: Respond with ONLY a JSON array of strings containing one description per row. Number of strings should be equal to the number of rows in the data.""",
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
- Number of Columns: {num_columns}

# Output Format:
Return ONLY a comma-separated list of headers, one for each column. Example:
ID,First Name,Last Name,Email,Department

Do not include any additional explanation or text.
"""
