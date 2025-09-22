import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from app.models.blocks import GroupType


@dataclass
class ChatDocCitation:
    content: str
    metadata: Dict[str, Any]
    chunkindex: int


def fix_json_string(json_str) -> str:
    """Fix control characters in JSON string values without parsing."""
    result = ""
    in_string = False
    escaped = False
    ascii_start = 32
    ascii_end = 127
    extended_ascii_end = 159
    for c in json_str:
        if escaped:
            # Previous character was a backslash, this character is escaped
            result += c
            escaped = False
            continue

        if c == "\\":
            # This is a backslash, next character will be escaped
            result += c
            escaped = True
            continue

        if c == '"':
            # This is a quote, toggle whether we're in a string
            in_string = not in_string
            result += c
            continue

        if in_string:
            # We're inside a string, escape control characters properly
            if c == "\n":
                result += "\\n"
            elif c == "\r":
                result += "\\r"
            elif c == "\t":
                result += "\\t"
            elif ord(c) < ascii_start or (ord(c) >= ascii_end and ord(c) <= extended_ascii_end):
                # Other control characters as unicode escapes
                result += f"\\u{ord(c):04x}"
            else:
                result += c
        else:
            # Not in a string, keep as is
            result += c

    return result



def normalize_citations_and_chunks(answer_text: str, final_results: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Normalize citation numbers in answer text to be sequential (1,2,3...)
    and create corresponding citation chunks with correct mapping
    """

    # Extract all citation numbers from the answer text
    citation_pattern = r'\[R(\d+)-(\d+)\]'
    matches = re.findall(citation_pattern, answer_text)

    if not matches:
        return answer_text, []


    unique_citations = []
    seen = set()

    for match in matches:
        citation_key = f"R{match[0]}-{match[1]}"  # Convert tuple to string format
        if citation_key not in seen:
            unique_citations.append(citation_key)
            seen.add(citation_key)

    citation_mapping = {}
    new_citations = []
    record_number = 0
    block_number_to_index = {}
    flattened_final_results = []
    seen = set()
    for i,doc in enumerate(final_results):
        virtual_record_id = doc.get("virtual_record_id")
        if virtual_record_id not in seen:
            record_number += 1
            seen.add(virtual_record_id)
        block_index = doc.get("block_index")
        block_type = doc.get("block_type")
        if block_type == GroupType.TABLE.value:
            _,child_results = doc.get("content")
            if child_results:
                for child in child_results:
                    child_block_index = child.get("block_index")
                    flattened_final_results.append(child)
                    block_number_to_index[f"R{record_number}-{child_block_index}"] = len(flattened_final_results) - 1
            else:
                flattened_final_results.append(doc)
                block_number_to_index[f"R{record_number}-{block_index}"] = len(flattened_final_results) - 1
        else:
            flattened_final_results.append(doc)
            block_number_to_index[f"R{record_number}-{block_index}"] = len(flattened_final_results) - 1

    # Create mapping from old citation keys to new sequential numbers
    for i, old_citation_key in enumerate(unique_citations):
        new_citation_num = i + 1

        # Get the corresponding chunk from final_results
        if old_citation_key in block_number_to_index:
            citation_mapping[old_citation_key] = new_citation_num
            chunk_index = block_number_to_index[old_citation_key]

            if 0 <= chunk_index < len(flattened_final_results):
                doc = flattened_final_results[chunk_index]
                new_citations.append({
                    "content": doc.get("content", ""),
                    "chunkIndex": new_citation_num,  # Use new sequential number
                    "metadata": doc.get("metadata", {}),
                    "citationType": "vectordb|document",
                })

    # Replace citation numbers in answer text
    def replace_citation(match) -> str:
        old_key = f"R{match.group(1)}-{match.group(2)}"
        return f"[{citation_mapping[old_key]}]" if old_key in citation_mapping else ""

    normalized_answer = re.sub(citation_pattern, replace_citation, answer_text)
    return normalized_answer, new_citations


def process_citations(llm_response, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Process the LLM response and extract citations from relevant documents with normalization.
    """
    try:
        # Handle the case where llm_response might be an object with a content field
        if hasattr(llm_response, "content"):
            response_content = llm_response.content
        elif isinstance(llm_response, dict) and "content" in llm_response:
            response_content = llm_response["content"]
        else:
            response_content = llm_response

        # Parse the LLM response if it's a string
        if isinstance(response_content, str):
            try:
                # Clean the JSON string before parsing
                cleaned_content = response_content.strip()
                # Handle nested JSON (sometimes response is JSON within JSON)
                if cleaned_content.startswith('"') and cleaned_content.endswith('"'):
                    cleaned_content = cleaned_content[1:-1].replace('\\"', '"')

                # Handle escaped newlines and other special characters
                cleaned_content = cleaned_content.replace("\\n", "\n").replace("\\t", "\t")

                # Apply our fix for control characters in JSON string values
                cleaned_content = fix_json_string(cleaned_content)

                # Try to parse the cleaned content
                response_data = json.loads(cleaned_content)
            except json.JSONDecodeError as e:
                # If regular parsing fails, try a more lenient approach
                try:
                    start_idx = cleaned_content.find("{")
                    end_idx = cleaned_content.rfind("}")

                    if start_idx >= 0 and end_idx > start_idx:
                        potential_json = cleaned_content[start_idx : end_idx + 1]
                        potential_json = fix_json_string(potential_json)
                        response_data = json.loads(potential_json)
                    else:
                        return {
                            "error": f"Failed to parse LLM response: {str(e)}",
                            "raw_response": response_content,
                        }
                except Exception as nested_e:
                    return {
                        "error": f"Failed to parse LLM response: {str(e)}, Nested error: {str(nested_e)}",
                        "raw_response": response_content,
                    }
        else:
            response_data = response_content

        # Create a result object (either use existing or create new)
        if isinstance(response_data, dict):
            result = response_data.copy()
        else:
            result = {"answer": str(response_data)}

        # Normalize citations in the answer if it exists
        if "answer" in result:
            normalized_answer, citations = normalize_citations_and_chunks(result["answer"], documents)
            result["answer"] = normalized_answer
            result["citations"] = citations
        else:
            # Fallback for cases where answer is not in a structured format
            normalized_answer, citations = normalize_citations_and_chunks(str(response_data), documents)
            result = {
                "answer": normalized_answer,
                "citations": citations
            }

        return result

    except Exception as e:
        import traceback
        return {
            "error": f"Citation processing failed: {str(e)}",
            "traceback": traceback.format_exc(),
            "raw_response": llm_response,
        }
