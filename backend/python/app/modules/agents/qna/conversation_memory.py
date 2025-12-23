import re
from typing import Any, Dict, List, Optional


class ConversationMemory:
    """
    Maintains conversation memory across query turns

    Extracts and preserves key information from previous interactions:
    - Tool execution results and summaries
    - Entity mentions (IDs, names, channels, etc.)
    - User intent and context
    - Decision history
    """

    @staticmethod
    def extract_tool_context_from_history(previous_conversations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Extract tool execution context from previous conversation

        Returns:
            Dict with tool_summaries, entities, and context
        """

        tool_summaries = []
        entities = {
            "channels": [],
            "users": [],
            "ids": [],
            "mentions": [],
            "urls": []
        }
        user_intents = []

        for conv in previous_conversations:
            role = conv.get("role")
            content = str(conv.get("content", ""))

            if role == "user_query":
                # Extract user intent
                user_intents.append(content)

                # Extract entity mentions
                entities["mentions"].extend(re.findall(r'@[\w\-\.]+', content))
                entities["channels"].extend(re.findall(r'#[\w\-]+', content))

            elif role == "bot_response":
                # Extract tool execution summaries
                tool_summary = ConversationMemory._extract_tool_summary(content)
                if tool_summary:
                    tool_summaries.append(tool_summary)

                # Extract entities from response
                entities["channels"].extend(re.findall(r'#[\w\-]+', content))
                entities["ids"].extend(re.findall(r'\b[A-Z0-9]{10,}\b', content))
                entities["urls"].extend(re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', content))

        # Deduplicate
        for key in entities:
            entities[key] = list(set(entities[key]))[:5]  # Keep top 5 unique

        return {
            "tool_summaries": tool_summaries,
            "entities": entities,
            "user_intents": user_intents[-3:],  # Last 3 intents
            "has_context": len(tool_summaries) > 0 or any(entities.values())
        }

    @staticmethod
    def _extract_tool_summary(content: str) -> Optional[Dict[str, Any]]:
        """Extract tool execution summary from bot response"""

        # Look for patterns indicating tool usage
        tool_indicators = [
            "retrieved", "fetched", "found", "searched",
            "created", "updated", "sent", "posted",
            "executed", "completed"
        ]

        has_tool_indicator = any(indicator in content.lower() for indicator in tool_indicators)

        if not has_tool_indicator:
            return None

        # Extract key information
        summary = {
            "action": None,
            "target": None,
            "result": None,
            "data_available": False
        }

        # Try to identify the action and target
        lower_content = content.lower()

        # Pattern: "retrieved X from Y"
        if "retrieved" in lower_content or "fetched" in lower_content:
            summary["action"] = "fetch"
            # Extract what was fetched
            if "messages" in lower_content:
                summary["target"] = "messages"
                summary["data_available"] = True
            elif "channels" in lower_content:
                summary["target"] = "channels"
                summary["data_available"] = True
            elif "users" in lower_content:
                summary["target"] = "users"
                summary["data_available"] = True

        # Pattern: "created X" or "sent X"
        elif "created" in lower_content or "sent" in lower_content or "posted" in lower_content:
            summary["action"] = "create/send"
            if "message" in lower_content:
                summary["target"] = "message"
            elif "post" in lower_content:
                summary["target"] = "post"

        # Pattern: "found X items/results"
        elif "found" in lower_content:
            summary["action"] = "search"
            numbers = re.findall(r'\b(\d+)\s+(?:channels?|messages?|items?|results?)', lower_content)
            if numbers:
                summary["result"] = f"{numbers[0]} items"
                summary["data_available"] = True

        return summary if summary["action"] else None

    @staticmethod
    def build_context_reminder(memory: Dict[str, Any]) -> str:
        """
        Build a concise context reminder from memory

        Returns:
            Concise reminder string for agent context
        """

        if not memory.get("has_context"):
            return ""

        reminders = []

        # Tool summaries
        tool_summaries = memory.get("tool_summaries", [])
        if tool_summaries:
            recent_tools = tool_summaries[-2:]  # Last 2 tool executions
            for i, summary in enumerate(recent_tools, 1):
                action = summary.get("action", "action")
                target = summary.get("target", "data")
                if summary.get("data_available"):
                    reminders.append(f"Previous turn {i}: {action} {target} (data still available)")

        # Entity context
        entities = memory.get("entities", {})
        if entities.get("channels"):
            reminders.append(f"Channels mentioned: {', '.join(entities['channels'][:3])}")
        if entities.get("mentions"):
            reminders.append(f"Users mentioned: {', '.join(entities['mentions'][:3])}")

        # User intent
        user_intents = memory.get("user_intents", [])
        if user_intents:
            reminders.append(f"Recent topic: {user_intents[-1][:50]}...")

        if not reminders:
            return ""

        return "\n\n📝 **Context from Previous Conversation**:\n" + "\n".join(f"- {r}" for r in reminders)

    @staticmethod
    def should_reuse_tool_results(
        current_query: str,
        previous_conversations: List[Dict[str, Any]]
    ) -> bool:
        """
        Determine if we can reuse tool results from previous turn

        Returns:
            True if follow-up that can use previous data
        """

        if not previous_conversations:
            return False

        # Check if current query is a simple follow-up
        follow_up_patterns = [
            r'^yes\b', r'^ok\b', r'^sure\b', r'^go ahead\b',
            r'^do it\b', r'^please\b', r'^now\b', r'^post it\b',
            r'send it', r'share it', r'create it', r'make it'
        ]

        query_lower = current_query.lower().strip()

        for pattern in follow_up_patterns:
            if re.match(pattern, query_lower):
                return True

        # Check if query is short and refers to previous context
        _SHORT_QUERY_WORD_COUNT = 5
        if len(current_query.split()) <= _SHORT_QUERY_WORD_COUNT:
            pronouns = ['it', 'that', 'those', 'them', 'this']
            if any(pronoun in query_lower for pronoun in pronouns):
                return True

        return False

    @staticmethod
    def enrich_query_with_context(
        query: str,
        previous_conversations: List[Dict[str, Any]]
    ) -> str:
        """
        Enrich follow-up query with context from previous conversation

        Returns:
            Enriched query with context
        """

        if not previous_conversations:
            return query

        memory = ConversationMemory.extract_tool_context_from_history(previous_conversations)

        # Check if it's a simple follow-up
        is_follow_up = ConversationMemory.should_reuse_tool_results(query, previous_conversations)

        if not is_follow_up:
            return query

        # Build enriched query
        enriched_parts = [f"**Current Request**: {query}"]

        # Add context reminder
        context_reminder = ConversationMemory.build_context_reminder(memory)
        if context_reminder:
            enriched_parts.append(context_reminder)

        # Add explicit instruction
        if memory.get("tool_summaries"):
            last_summary = memory["tool_summaries"][-1]
            if last_summary.get("data_available"):
                enriched_parts.append(
                    f"\n💡 **Note**: This appears to be a follow-up to your previous request. "
                    f"You mentioned wanting to {last_summary.get('action', 'work with')} "
                    f"{last_summary.get('target', 'data')}. "
                    f"The data from the previous turn is available - proceed with the action."
                )

        return "\n".join(enriched_parts)


def get_conversation_memory() -> ConversationMemory:
    """Get conversation memory instance"""
    return ConversationMemory()

