import logging
import sys
from typing import Any, Dict, List, Optional

from app.modules.agents.qna.chat_state import ChatState

# ============================================================================
# CONSTANTS
# ============================================================================

# Memory limits
MAX_MESSAGE_HISTORY = 20  # Keep last 20 messages max
MAX_TOOL_RESULTS = 15  # Keep last 15 tool results
MAX_DOCUMENT_SIZE = 5000  # Max chars per document
MAX_TOTAL_CONTEXT_SIZE = 100_000  # Max total context size (chars)

# Compression thresholds
COMPRESS_THRESHOLD = 10_000  # Compress content > 10KB
TRUNCATE_THRESHOLD = 50_000  # Truncate content > 50KB


# ============================================================================
# STATE PRUNING
# ============================================================================

def prune_state(state: ChatState, logger: Optional[logging.Logger] = None) -> ChatState:
    """
    Intelligently prune state to reduce memory footprint.

    Removes:
    - Old messages beyond limit
    - Old tool results beyond limit
    - Intermediate query processing data
    - Verbose analysis data
    - Redundant fields

    Keeps:
    - Recent messages
    - Recent tool results
    - Essential metadata
    - User context
    """
    if logger:
        logger.debug("🧹 Pruning state for memory efficiency...")

    # 1. Prune message history
    messages = state.get("messages", [])
    if len(messages) > MAX_MESSAGE_HISTORY:
        # Keep system message + last N messages
        system_msgs = [m for m in messages if hasattr(m, 'type') and m.type == 'system']
        recent_msgs = messages[-MAX_MESSAGE_HISTORY:]
        state["messages"] = system_msgs + recent_msgs
        if logger:
            logger.debug(f"   Pruned messages: {len(messages)} → {len(state['messages'])}")

    # 2. Prune tool results
    all_tool_results = state.get("all_tool_results", [])
    if len(all_tool_results) > MAX_TOOL_RESULTS:
        state["all_tool_results"] = all_tool_results[-MAX_TOOL_RESULTS:]
        if logger:
            logger.debug(f"   Pruned tool results: {len(all_tool_results)} → {MAX_TOOL_RESULTS}")

    # 3. Remove intermediate query processing data
    intermediate_fields = [
        "decomposed_queries",
        "rewritten_queries",
        "expanded_queries",
        "search_results"  # Keep only final_results
    ]
    for field in intermediate_fields:
        if field in state and state[field]:
            state[field] = []

    # 4. Compress query analysis (keep only essential)
    if "query_analysis" in state and state["query_analysis"]:
        analysis = state["query_analysis"]
        state["query_analysis"] = {
            "is_complex": analysis.get("is_complex", False),
            "needs_internal_data": analysis.get("needs_internal_data", False),
            "intent": analysis.get("intent", "unknown")
        }

    # 5. Clean up verbose tool metadata
    if "tool_execution_summary" in state:
        state["tool_execution_summary"] = {}

    if "tool_data_available" in state:
        state["tool_data_available"] = {}

    if logger:
        logger.debug("✅ State pruning complete")

    return state


# ============================================================================
# CONTEXT COMPRESSION
# ============================================================================

def compress_documents(documents: List[Dict], logger: Optional[logging.Logger] = None) -> List[Dict]:
    """
    Compress document list to reduce memory.

    - Truncate very long documents
    - Remove redundant metadata
    - Deduplicate similar documents
    """
    if not documents:
        return documents

    compressed = []
    seen_contents = set()

    for doc in documents:
        # Get content
        content = doc.get("page_content", "")

        # Skip duplicates (exact matches)
        content_hash = hash(content)
        if content_hash in seen_contents:
            continue
        seen_contents.add(content_hash)

        # Truncate if too long
        if len(content) > MAX_DOCUMENT_SIZE:
            content = content[:MAX_DOCUMENT_SIZE] + "... [truncated]"

        # Keep essential metadata only
        metadata = doc.get("metadata", {})
        essential_metadata = {
            "source": metadata.get("source"),
            "title": metadata.get("title"),
            "type": metadata.get("type"),
        }

        compressed.append({
            "page_content": content,
            "metadata": essential_metadata
        })

    if logger and len(documents) != len(compressed):
        logger.debug(f"   Compressed documents: {len(documents)} → {len(compressed)}")

    return compressed


def compress_context(context: str, max_size: int = MAX_TOTAL_CONTEXT_SIZE) -> str:
    """
    Compress context string if it exceeds max size.

    Uses intelligent truncation:
    - Keep beginning (context)
    - Keep end (recent info)
    - Truncate middle
    """
    if len(context) <= max_size:
        return context

    # Keep first 40% and last 40%, truncate middle
    keep_size = int(max_size * 0.4)

    start = context[:keep_size]
    end = context[-keep_size:]
    middle = f"\n\n... [truncated {len(context) - (2 * keep_size)} characters] ...\n\n"

    return start + middle + end


# ============================================================================
# MESSAGE MANAGEMENT
# ============================================================================

def optimize_messages(messages: List, logger: Optional[logging.Logger] = None) -> List:
    """
    Optimize message list for efficiency.

    - Remove redundant messages
    - Compress long message content
    - Keep conversation flow intact
    """
    _MIN_MESSAGES_FOR_OPTIMIZATION = 5
    if not messages or len(messages) <= _MIN_MESSAGES_FOR_OPTIMIZATION:
        return messages

    optimized = []

    for msg in messages:
        # Always keep system messages
        if hasattr(msg, 'type') and msg.type == 'system':
            optimized.append(msg)
            continue

        # Compress long message content
        if hasattr(msg, 'content') and isinstance(msg.content, str):
            if len(msg.content) > COMPRESS_THRESHOLD:
                compressed_content = compress_context(msg.content, COMPRESS_THRESHOLD)
                # Create new message with compressed content
                msg_dict = msg.dict() if hasattr(msg, 'dict') else {'content': compressed_content}
                msg_dict['content'] = compressed_content
                # Keep the message but with compressed content
                optimized.append(msg)
            else:
                optimized.append(msg)
        else:
            optimized.append(msg)

    # Ensure we don't exceed max history
    if len(optimized) > MAX_MESSAGE_HISTORY:
        system_msgs = [m for m in optimized if hasattr(m, 'type') and m.type == 'system']
        recent_msgs = [m for m in optimized if not (hasattr(m, 'type') and m.type == 'system')][-MAX_MESSAGE_HISTORY:]
        optimized = system_msgs + recent_msgs

    if logger and len(messages) != len(optimized):
        logger.debug(f"   Optimized messages: {len(messages)} → {len(optimized)}")

    return optimized


# ============================================================================
# MEMORY MONITORING
# ============================================================================

def get_state_memory_size(state: ChatState) -> Dict[str, Any]:
    """
    Calculate approximate memory size of state.

    Returns breakdown by field.
    """
    sizes = {}
    total_size = 0

    for key, value in state.items():
        try:
            size = sys.getsizeof(value)

            # For lists/dicts, get deep size
            if isinstance(value, (list, dict)):
                if isinstance(value, list):
                    size += sum(sys.getsizeof(item) for item in value if item is not None)
                elif isinstance(value, dict):
                    size += sum(sys.getsizeof(k) + sys.getsizeof(v) for k, v in value.items())

            sizes[key] = size
            total_size += size
        except Exception:
            sizes[key] = 0

    return {
        "total_bytes": total_size,
        "total_kb": round(total_size / 1024, 2),
        "total_mb": round(total_size / (1024 * 1024), 2),
        "by_field": {k: f"{v/1024:.2f} KB" for k, v in sorted(sizes.items(), key=lambda x: x[1], reverse=True) if v > 0}
    }


def check_memory_health(state: ChatState, logger: Optional[logging.Logger] = None) -> Dict[str, Any]:
    """
    Check memory health and provide recommendations.

    Returns:
        Health report with warnings and recommendations
    """
    memory_info = get_state_memory_size(state)

    warnings = []
    recommendations = []

    # Check total size
    total_mb = memory_info["total_mb"]
    _LARGE_STATE_SIZE_MB = 10
    if total_mb > _LARGE_STATE_SIZE_MB:
        warnings.append(f"State size is large: {total_mb:.2f} MB")
        recommendations.append("Consider pruning state with prune_state()")

    # Check message count
    message_count = len(state.get("messages", []))
    if message_count > MAX_MESSAGE_HISTORY:
        warnings.append(f"Too many messages: {message_count}")
        recommendations.append(f"Limit messages to {MAX_MESSAGE_HISTORY}")

    # Check tool results count
    tool_count = len(state.get("all_tool_results", []))
    if tool_count > MAX_TOOL_RESULTS:
        warnings.append(f"Too many tool results: {tool_count}")
        recommendations.append(f"Limit tool results to {MAX_TOOL_RESULTS}")

    # Check document count
    doc_count = len(state.get("final_results", []))
    _MAX_DOCUMENTS_WARNING = 50
    if doc_count > _MAX_DOCUMENTS_WARNING:
        warnings.append(f"Too many documents: {doc_count}")
        recommendations.append("Consider compressing documents")

    health_status = "healthy" if not warnings else "needs_optimization"

    report = {
        "status": health_status,
        "memory_info": memory_info,
        "warnings": warnings,
        "recommendations": recommendations
    }

    if logger and warnings:
        logger.warning(f"⚠️ Memory health: {health_status}")
        for warning in warnings:
            logger.warning(f"   - {warning}")

    return report


# ============================================================================
# AUTO-OPTIMIZATION
# ============================================================================

def auto_optimize_state(state: ChatState, logger: Optional[logging.Logger] = None) -> ChatState:
    """
    Automatically optimize state based on memory health.

    Applies optimizations only when needed.
    """
    if logger:
        logger.debug("🔍 Checking state memory health...")

    health = check_memory_health(state, logger)

    if health["status"] == "needs_optimization":
        if logger:
            logger.info("🔧 Auto-optimizing state...")

        # Apply optimizations
        state = prune_state(state, logger)

        # Compress documents
        if "final_results" in state and state["final_results"]:
            state["final_results"] = compress_documents(state["final_results"], logger)

        # Optimize messages
        if "messages" in state and state["messages"]:
            state["messages"] = optimize_messages(state["messages"], logger)

        if logger:
            logger.info("✅ State optimization complete")
            # Check health again
            new_health = check_memory_health(state)
            logger.info(f"   Memory: {new_health['memory_info']['total_mb']:.2f} MB")
    else:
        if logger:
            logger.debug("✅ State memory is healthy")

    return state

