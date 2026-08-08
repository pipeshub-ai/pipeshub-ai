from __future__ import annotations

from app.agent_loop_lib.roles.base import Role

WEB_SEARCH_ROLE = Role(
    name="web_search",
    description="Searches the web and returns relevant results.",
    system_prompt=(
        "You are a web research agent.\n\n"
        "Guidelines:\n"
        "- Run 2-3 targeted searches to gather the key facts.\n"
        "- If a search result is clearly relevant, scrape that page once — don't scrape every result.\n"
        "- Once you have enough information to answer, stop searching and call task_complete with a clear summary.\n"
        "- Never search for the same topic twice. Use the information you already have."
    ),
    allowed_tools=["web_search", "web_scrape", "task_complete"],
    capabilities=["web_search", "retrieval"],
)
