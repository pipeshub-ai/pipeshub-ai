import type { TFunction } from 'i18next';
import { VERBS, appOf, bareTool, scheduleToolOf, scheduleVerb, shownName } from '@/chat/interaction-wording';
import { humanizeToolName } from '@/chat/utils/tool-display';
import type { Words } from './words';

/**
 * Every approval wording of the job screens in one place: the action in plain words with its count folded in
 * ("Create 2 issues in Jira"), never "calls", never an id. The card asks it ("Create 2 issues in Jira?"), the trail
 * row says it waits ("Create 2 issues in Jira · waiting for you") and then stamps it ("Approved by you: create 2
 * issues in Jira"). The built-in tools keep their own counted verbs ("Search the web 3 times").
 */

export interface AskedCall {
  toolName: string;
  displayName?: string | null;
}

/** "issue" to "issues", "query" to "queries", "batch" to "batches". The words come from tool names, in English. */
function plural(word: string): string {
  if (/[^aeiou]y$/i.test(word)) return `${word.slice(0, -1)}ies`;
  if (/(s|x|z|ch|sh)$/i.test(word)) return `${word}es`;
  return `${word}s`;
}

/** What the call does, as a person names it: "Create issue", "Send message". */
function actionWords(call: AskedCall): string {
  const bare = bareTool(call.toolName);
  const display = shownName(call.displayName ?? undefined);
  const dotted = display?.split(' · ');
  if (dotted && dotted.length === 2) return humanizeToolName(dotted[1]);
  if (display?.trim()) return display.trim();
  return humanizeToolName(bare);
}

/** The action with its count: "Create issue", "Create 2 issues", "Search the web 3 times". */
export function countedAction(t: Words, call: AskedCall, count = 1): string {
  const bare = bareTool(call.toolName);
  if (VERBS.has(bare)) return (t as unknown as TFunction)(`interaction.verb.${bare}`, { count });
  const scheduleWords = scheduleVerb(t as unknown as TFunction, call, count);
  if (scheduleWords !== null) return scheduleWords;
  const words = actionWords(call);
  if (count <= 1) return words;
  const parts = words.split(' ');
  if (parts.length < 2) return t('ui.approval.times', { action: words, count });
  const last = parts.length - 1;
  return [parts[0], String(count), ...parts.slice(1, last), plural(parts[last])].join(' ');
}

/** "Create 2 issues in Jira": the app named once, when the action does not already say it. */
export function plainAction(t: Words, call: AskedCall, count = 1): string {
  const action = countedAction(t, call, count);
  const app = VERBS.has(bareTool(call.toolName)) || scheduleToolOf(call) !== null ? null : appOf({ toolName: call.toolName, displayName: call.displayName ?? undefined });
  return app && !action.toLowerCase().includes(app.toLowerCase()) ? t('ui.approval.actionIn', { action, app }) : action;
}

/** A word that starts a sentence, set mid-sentence: "create 2 issues", but "Jira" and "PR" stay as they are. */
export function midSentence(text: string): string {
  if (text.length < 2 || /^[A-Z]{2}/.test(text)) return text;
  return text.charAt(0).toLowerCase() + text.slice(1);
}

/** The card's question: "Create 2 issues in Jira?". */
export const askQuestion = (t: Words, call: AskedCall, count = 1): string => t('ui.approval.question', { action: plainAction(t, call, count) });

/** The trail row while it waits: "Create 2 issues in Jira · waiting for you". */
export const askWaiting = (t: Words, call: AskedCall, count = 1): string => t('ui.trail.waitingFor', { action: plainAction(t, call, count) });

const STAMPS: Readonly<Record<string, string>> = {
  approved: 'ui.trail.stamp.approved',
  allowed: 'ui.trail.stamp.approved',
  conversation: 'ui.trail.stamp.approvedChat',
  answered: 'ui.trail.stamp.happened',
  denied: 'ui.trail.stamp.declined',
  declined: 'ui.trail.stamp.declined',
  expired: 'ui.trail.stamp.expired',
};

/** The stamp a decision leaves: "Approved by you: create 2 issues in Jira". Anything not decided did not go ahead. */
export function askStamp(t: Words, decision: string, call: AskedCall, count = 1): string {
  return t(STAMPS[decision] ?? 'ui.trail.stamp.closed', { action: midSentence(plainAction(t, call, count)) });
}
