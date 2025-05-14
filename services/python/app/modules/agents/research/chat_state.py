
from typing import Any, Dict, List, Optional

from typing_extensions import TypedDict

class ChatMessage(TypedDict):
    role: str
    content: str

class Document(TypedDict):
    page_content: str
    metadata: Dict[str, Any]
    
class ChatState(TypedDict):
    query: str
    limit: int
    messages: List[ChatMessage]
    previous_conversations: List[Dict[str, str]]
    should_decompose: bool  # Renamed from decompose_query to avoid conflict
    filters: Optional[Dict[str, Any]]
    retrieval_mode: str
    decomposed_queries: List[Dict[str, str]]
    rewritten_queries: List[str]
    expanded_queries: List[str]
    search_results: List[Document]
    final_results: List[Document]
    user_info: Optional[Dict[str, Any]]
    org_info: Optional[Dict[str, Any]]
    response: Optional[str]
    error: Optional[Dict[str, Any]]
    org_id: str
    user_id: str
    send_user_info: bool

def build_initial_state(chat_query: Dict[str, Any], user_info: Dict[str, Any]) -> ChatState:
    """Build the initial state from the chat query and user info"""
    return {
        "query": chat_query.get("query", ""),
        "limit": chat_query.get("limit", 50),
        "messages": [{"role": "system", "content": "You are an enterprise questions answering expert"}],
        "previous_conversations": chat_query.get("previousConversations", []),
        "should_decompose": chat_query.get("useDecomposition", True),  # Renamed
        "filters": chat_query.get("filters"),
        "retrieval_mode": chat_query.get("retrieval_mode", "HYBRID"),
        "decomposed_queries": [],
        "rewritten_queries": [],
        "expanded_queries": [],
        "search_results": [],
        "final_results": [],
        "user_info": None,
        "org_info": None,
        "response": None,
        "error": None,
        "org_id": user_info.get("orgId", ""),
        "user_id": user_info.get("userId", ""),
        "send_user_info": user_info.get("sendUserInfo", True)
    }
