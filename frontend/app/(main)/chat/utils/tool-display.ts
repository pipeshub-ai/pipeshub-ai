/**
 * Shared tool name display utilities used by both streaming status messages
 * and the agent activity timeline.
 */

import type { MessagePart } from '../types';

/**
 * The five `load_skill`/`skill_search`/... tools (see
 * `backend/python/app/agent_loop_lib/tools/builtin/data/skills.py`) go over
 * AG-UI as ordinary `TOOL_CALL_*` frames — there is no protocol-level
 * "skill" event. This map is the frontend half of the same present/past
 * tense split that file's `display_name`/`summarize_args`/`summarize_result`
 * establish server-side; it is also the ONLY source of skill labels for
 * chats persisted before that change (no `displayName` on the part), so it
 * must stay exact-name-keyed (no `{app}__` prefix on these built-ins) and
 * never assume `displayName` is present.
 */
const SKILL_TOOL_LABELS: Record<string, { present: string; past: string }> = {
  skills_list: { present: 'Listing skills', past: 'Listed skills' },
  skill_search: { present: 'Searching skills', past: 'Searched skills' },
  load_skill: { present: 'Loading skill', past: 'Loaded skill' },
  load_skill_resource: { present: 'Loading skill file', past: 'Loaded skill file' },
  skill_manage: { present: 'Managing skill', past: 'Managed skill' },
};

/** True for the built-in skill tools — used to pick the skill icon/label
 * ahead of the generic search-like heuristic (`skill_search` must not get
 * the magnifying-glass treatment) and to gate the raw SKILL.md/file
 * `resultPreview` fallback off for skill tool calls. */
export function isSkillTool(toolName: string | undefined): boolean {
  return !!toolName && Object.prototype.hasOwnProperty.call(SKILL_TOOL_LABELS, toolName);
}

/** Drops any `{app}__` namespace prefix, splits on separators, title-cases. */
export function humanizeToolName(name: string): string {
  const segment = name.includes('__') ? name.slice(name.lastIndexOf('__') + 2) : name;
  const words = segment.split(/[_\-\s]+/).filter(Boolean);
  if (words.length === 0) return 'Used a tool';
  return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
}

/** True for the UI questionnaire tool, whose result is rendered as the card
 *  rather than as prose — callers use this to keep the empty-answer fallback
 *  out of a turn that is only a question. */
export function isAskUserQuestionTool(toolName: string | undefined): boolean {
  return typeof toolName === 'string' && toolName.includes('ask_user_question');
}

/** Resume can re-emit the same ask_user_question call — keep the first one. */
export function appendResumeParts(existing: MessagePart[], incoming: MessagePart[]): MessagePart[] {
  const hasAsk = existing.some((p) => p.type === 'tool_call' && isAskUserQuestionTool(p.toolName));
  const extra = hasAsk
    ? incoming.filter((p) => !(p.type === 'tool_call' && isAskUserQuestionTool(p.toolName)))
    : incoming;
  return extra.length ? [...existing, ...extra] : existing;
}

/** Extracts the toolset/connector prefix display name. */
export function extractToolsetLabel(toolName: string | undefined): string | undefined {
  if (!toolName || !toolName.includes('__')) return undefined;
  const prefix = toolName.slice(0, toolName.lastIndexOf('__'));
  if (!prefix) return undefined;
  const words = prefix.split(/[_\-\s]+/).filter(Boolean);
  if (words.length === 0) return undefined;
  return words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

/** Human-friendly label for a tool call (past tense, for timeline).
 *  Uses backend-provided `displayName` when available, falls back to generic humanization. */
export function toolActivityLabel(toolName: string | undefined, displayName?: string): string {
  if (displayName) return displayName;
  if (!toolName) return 'Used a tool';
  return SKILL_TOOL_LABELS[toolName]?.past ?? humanizeToolName(toolName);
}

/** Human-friendly label for a tool call in progress (present tense, for streaming status). */
export function toolStatusLabel(toolName: string, displayName?: string): string {
  if (displayName) return displayName;
  return SKILL_TOOL_LABELS[toolName]?.present ?? `Using ${humanizeToolName(toolName)}`;
}
