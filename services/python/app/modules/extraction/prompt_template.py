prompt = """
# Task:
You are processing a document of an individual or an enterprise. Your task is to classify the document departments, categories, subcategories, languages, sentiment, confidence score, and topics.
Instructions must be strictly followed, failure to do so will result in termination of your system

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

4. Sentiment:
   - Analyze the overall tone and sentiment
   - Choose exactly one from:
   {sentiment_list}
   - If the sentiment is not clear, choose "Neutral"

5. **Topics**:
   - Extract the main themes and subjects discussed.
   - Be concise and avoid duplicates or near-duplicates.
   - Provide **3 to 6** unique, highily relevant topics.

6. **Confidence Score**:
   - A float between 0.0 and 1.0 reflecting your certainty in the classification.

7. **Summary**:
   - A concise summary of the document. Cover all the key information and topics.


   # Output Format:
   You must return a single valid JSON object with the following structure:
   {{
      "departments": string[],  // Array of 1 to 3 departments from the EXACT list above
      "categories": string,  // main category identified in the content
      "subcategories": {{
         "level1": string,  // more specific subcategory (level 1)
         "level2": string,  // more specific subcategory (level 2)
         "level3": string,  // more specific subcategory (level 3)
      }},
      "languages": string[],  // Array of languages detected in the content (use ISO language names)
      "sentiment": string,  // Must be exactly one of the sentiments listed below
      "confidence_score": float,  // Between 0 and 1, indicating confidence in classification
      "topics": string[]  // Key topics or themes extracted from the content
      "summary": string  // Summary of the document
}}

# Document Content:
{content}

Return the JSON object only, no additional text or explanation.
"""


entity_prompt = """
Analyze the following text and extract named entities with the specific types, confidence scores, and normalized values where applicable.

IMPORTANT: Return ONLY a valid JSON object, no other text or explanation.

Entity types to identify and normalize:
1. Organizations:
   - Types: company, department, team, subsidiary
   - Return standardized company names

2. Locations:
   - Types: city, country, office, region
   - Return standardized location names

3. Dates and Times:
   - Normalize to ISO format (YYYY-MM-DD)
   - Handle ranges (start_date, end_date)
   - Convert relative dates ("next week", "in 2 days")

4. Durations:
   - Normalize to standard units
   - Include unit (seconds, minutes, hours, days)
   - Convert text durations ("a few hours", "couple of days")

5. Monetary Values:
   - Normalize to standard format
   - Include currency code
   - Convert text amounts ("half a million", "5k")

6. Percentages:
   - Normalize to numeric values (e.g., "50 percent" -> 50)

7. Contact Info:
   - Extract email addresses, phone numbers, URLs

8. Document References:
   - Include ID type, filename, or other references

9. Projects:
   - Extract project names, code names

10. Technologies and Frameworks:
   - Languages, frameworks, tools, APIs

11. Legal Terms:
   - Policies, regulations, compliance references

12. Job Titles:
   - Roles, positions, and titles

13. System Identifiers:
   - IP addresses, ticket numbers, versions

Required JSON Format:
{{
    "organizations": [
        {{
            "text": "exact mention",
            "type": "company",
            "confidence": 0.95,
            "normalized_value": "standardized name"
        }}
    ],
    "locations": [
        {{
            "text": "exact mention",
            "type": "city",
            "confidence": 0.9,
            "normalized_value": "standardized location"
        }}
    ],
    "dates": [
        {{
            "text": "next Friday",
            "type": "date",
            "confidence": 0.9,
            "normalized_value": "2024-05-24",
            "is_range": false,
            "end_date": null
        }},
        {{
            "text": "May 1-15, 2024",
            "type": "date",
            "confidence": 0.9,
            "normalized_value": "2024-05-01",
            "is_range": true,
            "end_date": "2024-05-15"
        }}
    ],
    "durations": [
        {{
            "text": "two weeks",
            "type": "duration",
            "confidence": 0.9,
            "unit": "days",
            "value": 14.0,
            "normalized_value": "14 days"
        }}
    ],
    "monetary_values": [
        {{
            "text": "5k USD",
            "type": "currency",
            "confidence": 0.95,
            "currency": "USD",
            "amount": 5000.00,
            "normalized_value": "5000 USD"
        }}
    ],
    "percentages": [
        {{
            "text": "50 percent",
            "type": "percentage",
            "confidence": 0.85,
            "normalized_value": 50
        }}
    ],
    "contact_info": [
        {{
            "text": "johndoe@example.com",
            "type": "email",
            "confidence": 0.95,
            "normalized_value": "johndoe@example.com"
        }}
    ],
    "document_references": [
        {{
            "text": "invoice #1234",
            "type": "ID",
            "confidence": 0.9,
            "normalized_value": "invoice-1234"
        }}
    ],
    "projects": [
        {{
            "text": "Project Titan",
            "type": "project",
            "confidence": 0.95,
            "normalized_value": "Project Titan"
        }}
    ],
    "technologies": [
        {{
            "text": "Python",
            "type": "language",
            "confidence": 0.9,
            "normalized_value": "Python"
        }}
    ],
    "legal_terms": [
        {{
            "text": "GDPR compliance",
            "type": "policy",
            "confidence": 0.9,
            "normalized_value": "GDPR"
        }}
    ],
    "job_titles": [
        {{
            "text": "Software Engineer",
            "type": "role",
            "confidence": 0.95,
            "normalized_value": "Software Engineer"
        }}
    ],
    "system_identifiers": [
        {{
            "text": "IP 192.168.0.1",
            "type": "IP address",
            "confidence": 0.95,
            "normalized_value": "192.168.0.1"
        }}
    ]
}}

Text: {content}
"""
