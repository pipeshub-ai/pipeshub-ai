# utils/tool_loop.py
from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage


async def call_llm_with_tools(
    llm: BaseChatModel,
    tools: list,
    messages: List[Dict[str, Any]],
    tool_runtime_kwargs: Dict[str, Any],
    max_tool_hops: int = 4,
) -> AIMessage:
    """
    Bind tools to LLM and resolve tool calls iteratively.
    Returns final AIMessage with no pending tool calls.
    """
    llm_with_tools = llm.bind_tools(tools)
    ai: AIMessage = await llm_with_tools.ainvoke(messages)

    hops = 0
    while isinstance(ai, AIMessage) and getattr(ai, "tool_calls", None):
        if hops >= max_tool_hops:
            break

        tool_msgs: List[ToolMessage] = []
        for call in ai.tool_calls:
            name = call["name"]
            args = call.get("args", {}) or {}
            call_id = call.get("id")
            tool = next((t for t in tools if t.name == name), None)

            if tool is None:
                tool_msgs.append(
                    ToolMessage(
                        content=json.dumps({"ok": False, "error": f"Unknown tool: {name}"}),
                        tool_call_id=call_id,
                    )
                )
                continue

            try:
                result = await tool.arun(**args, **tool_runtime_kwargs)
            except Exception as e:
                result = json.dumps({"ok": False, "error": str(e)})

            tool_msgs.append(ToolMessage(content=result, tool_call_id=call_id))

        # Feed back the tool results
        messages.append(ai)
        messages.extend(tool_msgs)
        ai = await llm_with_tools.ainvoke(messages)
        hops += 1

    return ai
