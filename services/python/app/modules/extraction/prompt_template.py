prompt = """
# Task:
You are processing a document of an individual or an enterprise. Your task is to classify the document departments, categories, subcategories, languages, sentiment, confidence score, topics, and sections covered.
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
   - Provide **3 to 6** unique, highly relevant topics.

6. **Section Numbers Covered**:
   - Identify and extract only explicitly mentioned section numbers, letters, or identifiers from the content.
   - Look for clear patterns such as:
     - "Section 1", "Section 1.1"
     - "1.2", "2.3.1"
     - "Part A", "Appendix B"
     - "Chapter 3"
     - "A.", "B.1.", "D." at the start of paragraphs or lines
   - Do not infer section coverage unless it is clearly labeled or numbered in the text.
   - Return an array of unique section identifiers as strings, without prefixes like "Section" or "Chapter".
   - If no section identifiers are found, return an empty list.
   - Examples:
     - "Section 1.1" → "1.1"
     - "Appendix A" → "A"
     - "C. Complementary Controls" → "C"
     - "Section 2.3.1" → "2.3.1"
     - Text without any section labels → []

7. **Confidence Score**:
   - A float between 0.0 and 1.0 reflecting your certainty in the classification.

8. **Summary**:
   - A concise summary of the document. Cover all the key entities, information, and topics.
   - The summary size should be proportional to the document size.
   - The summary should be in the same language as the document.

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
      "topics": string[],  // Key topics or themes extracted from the content
      "summary": string,  // Summary of the document
      "sectionNumbersCovered": string[]  // Array of section numbers covered in this content, e.g., ["1.1", "2", "A", "C.1"]
}}

# Document Content:
{content}

Return the JSON object only, no additional text or explanation.
"""
