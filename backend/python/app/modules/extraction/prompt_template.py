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
      "category": string,  // main category identified in the content
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


prompt_for_image_description = """
Describe the image below. Response should only be the description, no other text or explanation.
"""

prompt_for_document_extraction = """
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
      "category": string,  // main category identified in the content
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

Return the JSON object only, no additional text or explanation.
"""
