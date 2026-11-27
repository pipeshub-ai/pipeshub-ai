import type { ToolStepFacts } from '@/chat/types';
import { humanizeToolName } from '@/chat/utils/tool-display';
import type { Words } from './words';

/**
 * A tool step as a plain title (design rule: no run ids, raw JSON or engine internals in the main surfaces).
 *
 * With the worker's typed facts (WP-112) the title says what happened to what: "Read Pricing, Acme", "Searched the
 * web for "PAY-1423"". Without them it names the tool in words ("Used Search docs"); the server's human summary of
 * the arguments may follow as the detail, but anything that looks like data (JSON, a bare id) is dropped, not shown.
 */

/**
 * `jira__create_issue`, `goldendocs__search_docs` and `mcp_e2esaas_create_ticket` read as "Create issue", "Search docs"
 * and "Create ticket", in the chat's words (`humanizeToolName`); a display name "E2Esaas · create_ticket" by its tool.
 */
export function toolLabel(toolName: string, displayName?: string | null): string {
  const shown = displayName?.trim();
  const dot = shown?.indexOf(' · ') ?? -1;
  if (shown && dot > 0) return humanizeToolName(shown.slice(dot + 3));
  if (shown) return shown;
  return toolName ? humanizeToolName(toolName) : toolName;
}

const UUIDISH = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|\b[0-9a-f]{24,}\b|\bix\.[a-z]+\.[0-9a-z]+/i;

/** True for text a person should not be shown as-is: JSON, or something carrying an id. */
export function looksLikeData(text: string | null | undefined): boolean {
  if (!text) return false;
  const s = text.trim();
  return /^[[{]/.test(s) || /"\s*:\s*/.test(s) || UUIDISH.test(s);
}

/** Text worth a second line, or nothing. */
export function plainDetail(text: string | null | undefined): string | undefined {
  if (!text) return undefined;
  const s = text.trim();
  if (!s || looksLikeData(s)) return undefined;
  return s.length > 160 ? `${s.slice(0, 157)}...` : s;
}

export function stepTitle(step: ToolStepFacts, t: Words): string | null {
  const target = step.action === 'readPage' ? (step.title ?? step.target) : step.target;
  if (!target) return null;
  const quoted = step.action === 'webSearch' || step.action === 'searchFiles' ? `“${target}”` : target;
  return t(`ui.trail.${step.action}`, { target: quoted });
}

export interface ToolCallFacts {
  toolName: string;
  displayName?: string | null;
  argsSummary?: string | null;
  step?: ToolStepFacts;
}

export function toolTitle(call: ToolCallFacts, t: Words): { title: string; detail?: string } {
  if (call.toolName === 'code_task') return { title: t('ui.trail.handedOff') };
  const fromFacts = call.step ? stepTitle(call.step, t) : null;
  if (fromFacts) return { title: fromFacts };
  return { title: t('ui.trail.usedTool', { tool: toolLabel(call.toolName, call.displayName) }), detail: plainDetail(call.argsSummary) };
}

/** The glyph for a tool step. */
export function toolIcon(call: Pick<ToolCallFacts, 'toolName' | 'step'>): string {
  switch (call.step?.action) {
    case 'readPage':
      return 'article';
    case 'webSearch':
      return 'travel_explore';
    case 'findFiles':
    case 'searchFiles':
    case 'listFiles':
      return 'folder_open';
    case 'readFile':
      return 'description';
    default:
      return call.toolName === 'code_task' ? 'terminal' : 'build';
  }
}
