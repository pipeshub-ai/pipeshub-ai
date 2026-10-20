import type {
  McpToolInfo,
  McpToolKind,
  McpToolKindSource,
  McpToolRule,
  McpToolRules,
} from './types';

/** One row of the tool approvals list. */
export interface McpRuleTool {
  name: string;
  title?: string;
  description?: string;
  /** What the tool does to data; undefined when it isn't known (a tool the server doesn't list now). */
  kind?: McpToolKind;
  kindSource?: McpToolKindSource;
  /** False for a tool that has a rule but isn't in the server's current list. */
  offered?: boolean;
}

// The backend's words (`app/agents/mcp/tool_kind.py`); both test files check the same names.
const DELETING_WORDS = new Set(
  [
    ['delete', 'deletes', 'deleted', 'deleting', 'deletion'],
    ['remove', 'removes', 'removed', 'removing', 'removal'],
    ['destroy', 'destroys', 'destroyed', 'destroying'],
    ['drop', 'drops', 'dropped', 'dropping'],
    ['purge', 'purges', 'purged', 'purging'],
    ['erase', 'erases', 'erased', 'erasing'],
    ['wipe', 'wipes', 'wiped', 'wiping'],
    ['truncate', 'truncates', 'truncated', 'truncating'],
    ['revoke', 'revokes', 'revoked', 'revoking'],
    ['uninstall', 'uninstalls', 'uninstalled', 'uninstalling'],
    ['reset', 'resets', 'resetting'],
  ].flat()
);
const WORD_RE = /[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+/g;

/** A deleting word in the tool's name: the backend denies it by default whatever the server says. */
export function deletesByName(name: string): boolean {
  const words: string[] = name.match(WORD_RE) ?? [];
  return words.some((word) => DELETING_WORDS.has(word.toLowerCase()));
}

function isKind(value: unknown): value is McpToolKind {
  return value === 'read' || value === 'write' || value === 'destructive';
}

function isKindSource(value: unknown): value is McpToolKindSource {
  return value === 'name' || value === 'server' || value === 'none';
}

/** A tool known only by its name: its kind is known when the name deletes. */
/** How a tool is named to a person: its title, else its name split into words, each word kept as
 * the server wrote it ("createJiraIssue" → "Create Jira Issue", "list_issues" → "List issues"). */
export function toolDisplayName(name: string, title?: string | null): string {
  if (title?.trim()) return title.trim();
  const words = name.replace(/([a-z0-9])([A-Z])/g, '$1 $2').split(/[_\-\s./]+/).filter(Boolean);
  if (words.length === 0) return name;
  return words.map((word, i) => (i === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word)).join(' ');
}

export function ruleToolFromName(name: string, description?: string): McpRuleTool {
  return {
    name,
    ...(description ? { description } : {}),
    ...(deletesByName(name) ? { kind: 'destructive' as const, kindSource: 'name' as const } : {}),
  };
}

export function ruleToolsFromInfo(
  tools: Pick<McpToolInfo, 'name' | 'description' | 'annotations' | 'kind' | 'kindSource'>[]
): McpRuleTool[] {
  return tools.map((tool) => {
    const title = tool.annotations?.title;
    const known = isKind(tool.kind);
    return {
      ...ruleToolFromName(tool.name, tool.description ?? undefined),
      ...(typeof title === 'string' && title ? { title } : {}),
      ...(known ? { kind: tool.kind, ...(isKindSource(tool.kindSource) ? { kindSource: tool.kindSource } : {}) } : {}),
    };
  });
}

/** Whose tools the rules are for: an MCP server's, or a PipesHub toolset's. They start differently. */
export type ToolRuleSource = 'mcp' | 'toolset';

/** Matches the server (`tool_approvals.starting_rule`); an unknown kind shows as a tool that changes data. */
export function startingRule(kind: McpToolKind | undefined, source: ToolRuleSource = 'mcp'): McpToolRule {
  if (kind === 'read') return 'allow';
  if (source === 'toolset') return kind === 'destructive' ? 'ask' : 'allow';
  if (kind === 'destructive') return 'block';
  return 'ask';
}

/** The server's tools in its order, then any other tool that has a rule. */
export function toolRuleRows(tools: McpRuleTool[], namesWithRules: string[]): McpRuleTool[] {
  const listed = new Set(tools.map((tool) => tool.name));
  const others = [...new Set(namesWithRules)]
    .filter((name) => !listed.has(name))
    .sort((a, b) => a.localeCompare(b))
    .map((name) => ({ ...ruleToolFromName(name), offered: false }));
  return [...tools, ...others];
}

/** Keeps the rules that differ from a tool's starting rule. A tool whose kind isn't known keeps
 * its rule: its starting rule can't be told here. */
export function ownRulesToSave(
  rows: McpRuleTool[],
  rules: Record<string, McpToolRule>,
  source: ToolRuleSource = 'mcp'
): McpToolRules {
  const tools: Record<string, McpToolRule> = {};
  for (const row of rows) {
    const rule = rules[row.name];
    if (rule && (row.kind === undefined || rule !== startingRule(row.kind, source))) tools[row.name] = rule;
  }
  return { tools };
}
