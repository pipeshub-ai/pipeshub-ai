/**
 * What a schedule's actor is told when a fired run ends (TR-07): a short
 * summary of the answer and links to the files it made, in the app. Email and
 * Slack carry the schedule's name, the outcome and a link only: a run's answer
 * may hold what its sources hold, and it stays behind the app's sign-in.
 */
import type { RunEndedNotice } from '../ports/schedules';
import type { RunDigest } from '../projection/run-digest';

export const MAX_NOTICE_SUMMARY_CHARS = 280;
export const MAX_NOTICE_ARTIFACTS = 10;

export interface NoticeArtifact {
  name: string;
  link: string;
}

export interface RunEndDigest {
  summary: string;
  artifacts: NoticeArtifact[];
}

/** The `::artifact[name](record:id){…}` marker a saved answer carries per file (transcript.ts). */
const ARTIFACT_MARKER =
  /::artifact\[([^\]]*)\]\(record:([^)]+)\)(?:\{[^}]*\})?/g;

export const recordLink = (recordId: string): string =>
  `/record/${encodeURIComponent(recordId)}/`;

/** The chat a scheduled run lives in: an agent's conversation opens in that agent. */
export function conversationLink(sessionId: string, agentKey?: string): string {
  const params = new URLSearchParams();
  if (agentKey !== undefined && agentKey.length > 0) {
    params.set('agentId', agentKey);
  }
  params.set('conversationId', sessionId);
  return `/chat/?${params.toString()}`;
}

/** Markdown reduced to its words: links to their text, code, emphasis and headings to plain text. */
function plain(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\s*\[\d+\]/g, '')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/^\s{0,3}(?:#{1,6}|>|[-*+]|\d+[.)])\s+/gm, '')
    .replace(/(\*\*|__|\*|_|~~)(?=\S)([^*_~]*?\S)\1/g, '$2')
    .replace(/\|/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function truncated(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max - 1);
  const space = cut.lastIndexOf(' ');
  return `${(space > max / 2 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

/** The answer's opening words and the files it made, each once, in the order it names them. */
export function runEndDigest(answer: string): RunEndDigest {
  const artifacts: NoticeArtifact[] = [];
  const seen = new Set<string>();
  for (const match of answer.matchAll(ARTIFACT_MARKER)) {
    const recordId = match[2] as string;
    if (seen.has(recordId) || artifacts.length >= MAX_NOTICE_ARTIFACTS)
      continue;
    seen.add(recordId);
    artifacts.push({
      name: (match[1] ?? '').trim() || 'file',
      link: recordLink(recordId),
    });
  }
  return {
    summary: truncated(
      plain(answer.replace(ARTIFACT_MARKER, ' ')),
      MAX_NOTICE_SUMMARY_CHARS,
    ),
    artifacts,
  };
}

const OUTCOME: Readonly<Record<RunEndedNotice['status'], string>> = {
  completed: 'finished',
  failed: 'failed',
  cancelled: 'was cancelled',
};

/** "A scheduled run finished", in the words every channel uses. */
export const runEndTitle = (status: RunEndedNotice['status']): string =>
  `A scheduled run ${OUTCOME[status]}`;

/** The one line email and Slack carry: the schedule, the outcome, the link. */
export const runEndLine = (
  notice: Pick<RunEndedNotice, 'scheduleName' | 'status'>,
  link: string,
): string =>
  `"${notice.scheduleName}" ${OUTCOME[notice.status]}. Open it: ${link}`;

const plural = (count: number, one: string, many: string): string =>
  `${count} ${count === 1 ? one : many}`;

/** Spend in US dollars, to the cent; under a cent reads "under USD 0.01". */
export function usdText(usdMicros: number): string {
  if (usdMicros > 0 && usdMicros < 10_000) return 'under USD 0.01';
  return `USD ${(usdMicros / 1_000_000).toFixed(2)}`;
}

/**
 * What the run did, as the app's notice says it (G-07): counts in plain words,
 * never a record's content or an id. Empty when it did nothing worth naming.
 */
export function digestLine(digest: RunDigest): string {
  const facts: string[] = [];
  const { actions, records, approvals, files } = digest;
  if (actions.total > 0) {
    facts.push(
      `used ${plural(actions.total, 'tool', 'tools')}` +
        (actions.failed > 0 ? ` (${actions.failed} failed)` : ''),
    );
  }
  if (digest.commands.total > 0) {
    facts.push(`ran ${plural(digest.commands.total, 'command', 'commands')}`);
  }
  if (records.read > 0) {
    facts.push(`read ${plural(records.read, 'record', 'records')}`);
  }
  if (records.changed > 0)
    facts.push(`made ${plural(records.changed, 'change', 'changes')}`);
  const changedFiles = files.added + files.modified + files.deleted;
  if (changedFiles > 0) {
    facts.push(`changed ${plural(changedFiles, 'file', 'files')}`);
  }
  if (digest.artifacts.length > 0) {
    facts.push(`saved ${plural(digest.artifacts.length, 'file', 'files')}`);
  }
  if (approvals.asked > 0) {
    facts.push(
      `asked ${plural(approvals.asked, 'approval', 'approvals')}` +
        ` (${approvals.approved} approved, ${approvals.denied} denied` +
        (approvals.unanswered > 0
          ? `, ${approvals.unanswered} unanswered`
          : '') +
        ')',
    );
  }
  if (digest.questions.open) facts.push('is waiting for your answer');
  if (digest.cost !== null && digest.cost.usdMicros > 0) {
    facts.push(`cost ${usdText(digest.cost.usdMicros)}`);
  }
  if (facts.length === 0) return '';
  const line = facts.join(', ');
  return `${line.charAt(0).toUpperCase()}${line.slice(1)}.`;
}
