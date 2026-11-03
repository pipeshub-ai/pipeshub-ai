/**
 * The five `load_skill`/`skill_search`/... tools (see
 * `backend/python/app/agent_loop_lib/tools/builtin/data/skills.py`) go over
 * AG-UI as ordinary `TOOL_CALL_*` frames — there is no protocol-level
 * "skill" event. Mirrors the frontend's `tool-display.ts` map exactly so
 * Slack status lines read the same as the dashboard's; must stay
 * exact-name-keyed (no `{app}__` prefix on these built-ins) and never
 * assume `displayName` is present, since it covers chats/messages
 * persisted before the backend started setting it too.
 */
const SKILL_TOOL_LABELS: Record<string, { present: string; past: string }> = {
  skills_list: { present: "Listing skills", past: "Listed skills" },
  skill_search: { present: "Searching skills", past: "Searched skills" },
  load_skill: { present: "Loading skill", past: "Loaded skill" },
  load_skill_resource: { present: "Loading skill file", past: "Loaded skill file" },
  skill_manage: { present: "Managing skill", past: "Managed skill" },
};

export function isSkillTool(toolName: string | undefined): boolean {
  return (
    !!toolName &&
    Object.prototype.hasOwnProperty.call(SKILL_TOOL_LABELS, toolName)
  );
}

/** MCP tools are named `mcp_{server}_{tool}`; toolset tools carry an `{app}__` prefix instead. */
export function isMcpTool(toolName: string | undefined): boolean {
  return !!toolName && toolName.startsWith('mcp_') && !toolName.includes('__');
}

const WEB_TOOLS = new Set(['web_search', 'fetch_url']);

/** The web search and page-reading tools, with or without their `dynamic__` prefix. */
export function isWebTool(toolName: string | undefined): boolean {
  return !!toolName && WEB_TOOLS.has(toolName.replace(/^dynamic__/, ''));
}

/** The app a tool belongs to, as the dashboard tags it ("Knowledgegraph", "MCP"). */
export function extractToolsetLabel(
  toolName: string | undefined,
): string | undefined {
  // Built-in web tools are not a toolset: their `dynamic__` prefix would read as "Dynamic".
  if (!toolName || isWebTool(toolName)) return undefined;
  if (!toolName.includes('__')) {
    return isMcpTool(toolName) ? 'MCP' : undefined;
  }
  const prefix = toolName.slice(0, toolName.lastIndexOf('__'));
  if (!prefix) return undefined;
  const words = prefix.split(/[_\-\s]+/).filter(Boolean);
  if (words.length === 0) return undefined;
  return words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

/** A burst's app tag, as the dashboard's group shows it: the one app its tagged calls share. */
export function groupToolsetLabel(toolNames: string[]): string | undefined {
  const labels = [
    ...new Set(
      toolNames
        .map((name) => extractToolsetLabel(name))
        .filter((label): label is string => Boolean(label)),
    ),
  ];
  return labels.length === 1 ? labels[0] : undefined;
}

/** Drops any `{app}__` namespace prefix, splits on separators, title-cases. */
export function humanizeToolName(name: string): string {
  const segment = name.includes("__")
    ? name.slice(name.lastIndexOf("__") + 2)
    : name;
  const words = segment.split(/[_\-\s]+/).filter(Boolean);
  if (words.length === 0) return "Used a tool";
  return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
}

/** Past-tense label for completed tool rows in the activity timeline. */
export function toolActivityLabel(
  toolName: string | undefined,
  displayName?: string,
): string {
  if (displayName) return displayName;
  if (!toolName) return "Used a tool";
  return SKILL_TOOL_LABELS[toolName]?.past ?? humanizeToolName(toolName);
}

/** Present-tense label for in-progress tool status. */
export function toolStatusLabel(toolName: string, displayName?: string): string {
  if (displayName) return displayName;
  return SKILL_TOOL_LABELS[toolName]?.present ?? `Using ${humanizeToolName(toolName)}`;
}
