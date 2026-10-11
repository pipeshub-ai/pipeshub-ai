import type {
  McpCompanyToolRule,
  McpToolInfo,
  McpToolKind,
  McpToolKindSource,
  McpToolPolicy,
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

/** Matches the server (`tool_approvals.starting_rule`); an unknown kind shows as a tool that changes data. */
export function startingRule(kind: McpToolKind | undefined): McpToolRule {
  if (kind === 'read') return 'allow';
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
export function ownRulesToSave(rows: McpRuleTool[], rules: Record<string, McpToolRule>): McpToolRules {
  const tools: Record<string, McpToolRule> = {};
  for (const row of rows) {
    const rule = rules[row.name];
    if (rule && (row.kind === undefined || rule !== startingRule(row.kind))) tools[row.name] = rule;
  }
  return { tools };
}

/** Drops entries that say nothing: no rule and not allowed unattended. */
export function policyToSave(policy: Record<string, McpCompanyToolRule>): McpToolPolicy {
  const tools: Record<string, McpCompanyToolRule> = {};
  for (const [name, entry] of Object.entries(policy)) {
    const rule = entry.rule ?? null;
    const unattended = rule !== 'block' && entry.unattended === true;
    if (rule || unattended) tools[name] = { rule, unattended };
  }
  return { tools };
}
