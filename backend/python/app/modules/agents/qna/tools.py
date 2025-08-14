import json
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Optional, Type
from urllib.parse import quote_plus

import requests
from ddgs import DDGS
from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from app.modules.agents.qna.chat_state import ChatState


class WriteToFileInput(BaseModel):
    content: str = Field(description="Content to write to the file")
    filename: Optional[str] = Field(default=None, description="Optional filename, will use default if not provided")
    append: bool = Field(default=False, description="Whether to append to existing file or overwrite")


class WebSearchInput(BaseModel):
    query: str = Field(description="Search query to search on the web")
    max_results: Optional[int] = Field(default=5, description="Maximum number of search results to return (default: 5)")


class WebSearchTool(BaseTool):
    name: str = "web_search"
    description: str = "Search the web for current information. Use this when you need to find recent, up-to-date information that may not be in the knowledge base."
    args_schema: Type[BaseModel] = WebSearchInput

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, state: ChatState, **kwargs) -> None:
        super().__init__(**kwargs)
        self._state = state
        self._search_tool = None

    @property
    def state(self) -> ChatState:
        return self._state

    def _get_search_tool(self) -> DDGS | str | None:
        """Initialize the correct search tool"""
        if self._search_tool is None:
            try:
                # Try the new DDGS package first
                try:

                    self._search_tool = DDGS()
                    self._search_method = "ddgs"
                    self.state["logger"].debug("Using DDGS (new package) for search")
                except ImportError:
                    # Fallback to requests-based search if DDGS not available
                    try:
                        self._search_tool = "requests"
                        self._search_method = "requests"
                        self.state["logger"].debug("Using requests-based search as fallback")
                    except ImportError:
                        self._search_tool = None
                        self._search_method = "none"
                        self.state["logger"].error("No search implementation available")

            except Exception as e:
                self.state["logger"].error(f"Error initializing search tool: {e}")
                self._search_tool = None
                self._search_method = "none"

        return self._search_tool

    def _search_with_ddgs(self, query: str, max_results: int) -> list:
        """Search using the new DDGS package"""
        try:
            results = []

            # Try different DDGS API versions
            try:
                # Version 1: Try with positional query argument
                with DDGS() as ddgs:
                    search_results = ddgs.text(
                        query,  # Positional argument
                        max_results=max_results,
                        safesearch='moderate'
                    )

                    for result in search_results:
                        results.append({
                            'title': result.get('title', ''),
                            'url': result.get('href', ''),
                            'content': result.get('body', '')
                        })

            except (TypeError, ValueError) as e:
                self.state["logger"].debug(f"First DDGS method failed: {e}, trying alternative")

                # Version 2: Try with keywords parameter
                try:
                    with DDGS() as ddgs:
                        search_results = ddgs.text(
                            keywords=query,
                            max_results=max_results,
                            safesearch='moderate'
                        )

                        for result in search_results:
                            results.append({
                                'title': result.get('title', ''),
                                'url': result.get('href', ''),
                                'content': result.get('body', '')
                            })

                except (TypeError, ValueError) as e2:
                    self.state["logger"].debug(f"Second DDGS method failed: {e2}, trying basic")

                    # Version 3: Try basic search
                    try:
                        ddgs = DDGS()
                        search_results = ddgs.text(query, max_results=max_results)

                        for result in search_results:
                            results.append({
                                'title': result.get('title', ''),
                                'url': result.get('href', ''),
                                'content': result.get('body', '')
                            })

                    except Exception as e3:
                        self.state["logger"].error(f"All DDGS methods failed: {e3}")
                        return []

            return results

        except ImportError:
            self.state["logger"].error("DDGS package not available")
            return []
        except Exception as e:
            self.state["logger"].error(f"DDGS search failed: {e}")
            return []

    def _search_with_requests(self, query: str, max_results: int) -> list:
        """Fallback search using requests (basic web scraping)"""
        try:

            # Use DuckDuckGo's instant answer API or basic search
            encoded_query = quote_plus(query)

            # Try DuckDuckGo instant answer API first
            instant_url = f"https://api.duckduckgo.com/?q={encoded_query}&format=json&no_html=1&skip_disambig=1"

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            response = requests.get(instant_url, headers=headers, timeout=10)

            if response.status_code == HTTPStatus.OK:
                data = response.json()

                results = []

                # Check for abstract
                if data.get('Abstract'):
                    results.append({
                        'title': data.get('Heading', 'DuckDuckGo Result'),
                        'url': data.get('AbstractURL', ''),
                        'content': data.get('Abstract', '')
                    })

                # Check for related topics
                for topic in data.get('RelatedTopics', [])[:max_results-len(results)]:
                    if isinstance(topic, dict) and 'Text' in topic:
                        results.append({
                            'title': topic.get('Text', '')[:100] + '...',
                            'url': topic.get('FirstURL', ''),
                            'content': topic.get('Text', '')
                        })

                return results

            return []

        except Exception as e:
            self.state["logger"].error(f"Requests-based search failed: {e}")
            return []

    def _create_fallback_response(self, query: str) -> list:
        """Create a fallback response when all search methods fail"""
        return [{
            'title': f'Knowledge Base Response for: {query}',
            'url': '',
            'content': f'Unable to fetch current web results for "{query}". The AI will provide information based on its training data up to its knowledge cutoff date.'
        }]

    def _run(self, query: str, max_results: Optional[int] = 5) -> str:
        """Search the web with multiple fallback methods"""
        try:
            # Validate inputs
            if not query or not query.strip():
                return json.dumps({
                    "status": "error",
                    "message": "Search query cannot be empty"
                }, indent=2)

            query = str(query).strip()
            max_results = max(1, min(int(max_results) if max_results else 5, 10))

            self.state["logger"].info(f"Performing web search for: {query}")

            # Get search tool
            formatted_results = []

            # Try DDGS first
            if self._search_method == "ddgs":
                self.state["logger"].debug("Attempting search with DDGS package")
                formatted_results = self._search_with_ddgs(query, max_results)

            # Fallback to requests
            if not formatted_results and self._search_method == "requests":
                self.state["logger"].debug("Attempting search with requests fallback")
                formatted_results = self._search_with_requests(query, max_results)

            # If still no results, try simplified query
            if not formatted_results and self._search_method == "ddgs":
                simple_query = " ".join(query.split()[:3])
                self.state["logger"].debug(f"Trying simplified query: {simple_query}")
                formatted_results = self._search_with_ddgs(simple_query, max_results)

            # Return results or fallback
            if formatted_results:
                self.state["logger"].info(f"Web search successful: {len(formatted_results)} results")
                return json.dumps({
                    "status": "success",
                    "query": query,
                    "results": formatted_results,
                    "total_results": len(formatted_results),
                    "message": f"Found {len(formatted_results)} web search results"
                }, indent=2)
            else:
                self.state["logger"].warning(f"No web results found for: {query}")
                return json.dumps({
                    "status": "no_results",
                    "query": query,
                    "results": [],
                    "total_results": 0,
                    "message": f"No current web results found for '{query}'. AI will use existing knowledge instead.",
                    "fallback_to_knowledge": True
                }, indent=2)

        except Exception as e:
            error_msg = f"Web search error: {str(e)}"
            self.state["logger"].error(error_msg)
            return json.dumps({
                "status": "error",
                "message": error_msg,
                "fallback_to_knowledge": True
            }, indent=2)


class WriteToFileTool(BaseTool):
    name: str = "write_to_file"
    description: str = "Write content to a file. Useful for saving responses, reports, or any generated content."
    args_schema: Type[BaseModel] = WriteToFileInput

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, state: ChatState, **kwargs) -> None:
        super().__init__(**kwargs)
        self._state = state

    @property
    def state(self) -> ChatState:
        return self._state

    def _run(self, content: str, filename: Optional[str] = None, append: bool = False) -> str:
        """Write content to a file"""
        try:
            if not content:
                return json.dumps({
                    "status": "error",
                    "message": "Content cannot be empty"
                }, indent=2)

            content = str(content)

            if isinstance(append, str):
                append = append.lower() in ['true', '1', 'yes', 'on']
            elif not isinstance(append, bool):
                append = bool(append)

            # Determine file path
            if filename:
                file_path = Path(filename)
            elif self.state.get("output_file_path"):
                file_path = Path(self.state["output_file_path"])
            else:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                default_filename = f"ai_summary_{timestamp}.md"
                file_path = Path(default_filename)

            # Ensure directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Write file
            mode = "a" if append else "w"
            with open(file_path, mode, encoding="utf-8") as f:
                f.write(content)
                if append:
                    f.write("\n")

            self.state["logger"].info(f"Content written to file: {file_path}")

            return json.dumps({
                "status": "success",
                "file_path": str(file_path),
                "content_length": len(content),
                "operation": "append" if append else "write",
                "message": f"Successfully saved content to {file_path}"
            }, indent=2)

        except Exception as e:
            error_msg = f"Error writing to file: {str(e)}"
            self.state["logger"].error(error_msg)
            return json.dumps({
                "status": "error",
                "message": error_msg
            }, indent=2)


def get_agent_tools(state: ChatState) -> list:
    """Get available tools for the agent based on configuration"""
    tools = []
    enabled_tools = state.get("tools", [])

    if not enabled_tools:
        return tools

    available_tools = {
        "write_to_file": WriteToFileTool(state),
        "web_search": WebSearchTool(state),
    }

    for tool_name in enabled_tools:
        if tool_name in available_tools:
            tools.append(available_tools[tool_name])
        else:
            state["logger"].warning(f"Unknown tool name: {tool_name}")

    return tools


def get_tool_by_name(tool_name: str, state: ChatState) -> BaseTool:
    """Get a specific tool by name"""
    available_tools = {
        "write_to_file": WriteToFileTool(state),
        "web_search": WebSearchTool(state),
    }
    return available_tools.get(tool_name)


def get_all_available_tool_names() -> list:
    """Get list of all available tool names"""
    return ["write_to_file", "web_search"]
