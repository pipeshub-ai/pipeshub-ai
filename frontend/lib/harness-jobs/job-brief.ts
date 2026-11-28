import type { JobBrief } from '@/lib/contracts/harness/generated/ext/pipeshub/api/job-brief';
import type { JobBriefHistory } from '@/lib/contracts/harness/generated/ext/pipeshub/api/job-brief-history';
import { stepWords } from './action-words';
import { appName } from './app-name';
import { formatDateTime, formatTime } from './dates';
import { toolLabel } from './step-titles';
import type { Words } from './words';

/**
 * A job's brief (`GET …/runs/{runId}/brief`, ADR-UI-01 D3) as the open agent's Brief tab reads it: what it was
 * asked, what PipesHub sent it, what it kept back and why, and how far it reaches (design.md §6B.42, "the security
 * story"). The server sends codes; the words are the interface's, in every locale. No id reaches a row: a source is
 * counted, a tool is named by its app and name.
 */

export interface BriefRow {
  /** Material Symbols glyph. */
  icon: string;
  text: string;
  note?: string;
}

/**
 * The job's task as a person reads it (BGUI-10, design `worker-panel.tsx`: the task in a few plain sentences, nothing
 * else above the fold), from the parts the harness kept (`brief.task`): never the prompt composed for the engine.
 */
export interface BriefTaskView {
  /** The task: the chat agent's notes, else the request; null once the chat's content was erased. */
  text: string | null;
  /** The person's own words, when the task shown is the agent's notes on them. */
  asked: string | null;
  /** Its limits as facts: when it is due, the most it may spend, the file it delivers. */
  limits: string[];
  /** What it started from: the calls the chat made before handing over, in plain words. */
  carried: BriefRow[];
  /** What earlier work in the chat already produced, as the agent wrote it. */
  earlier: string | null;
}

export interface BriefView {
  /** The task the parent gave it; null once the chat's content was erased. */
  goal: string | null;
  task: BriefTaskView;
  sent: BriefRow[];
  withheld: BriefRow[];
  reach: BriefRow[];
  /** The connected sources its tools come from (ADR-OWD-07 D-DISC-2), each by its name and what it covers. */
  sources: BriefRow[];
}

const WITHHELD_ICON: Record<JobBrief['withheld'][number]['what'], string> = {
  conversation: 'forum',
  credentials: 'key',
  tools: 'build',
  knowledge: 'menu_book',
  skills: 'school',
  internet: 'language',
  files: 'draft',
};

const list = (items: ReadonlyArray<string>, max = 4): string => (items.length > max ? `${items.slice(0, max).join(', ')}, ...` : items.join(', '));

export function briefView(brief: JobBrief, t: Words, options: BriefOptions = {}): BriefView {
  const sent: BriefRow[] = [];
  const { files, sources, sourcesComplete, knowledge, skills, continuesRunId } = brief.sent;
  if (files.length) sent.push({ icon: 'draft', text: t('ui.brief.sent.files', { count: files.length }) });
  if (sources.length || !sourcesComplete) {
    sent.push({
      icon: 'description',
      text: sources.length ? t('ui.brief.sent.sources', { count: sources.length }) : t('ui.brief.sent.someSources'),
      ...(sourcesComplete ? {} : { note: t('ui.brief.sent.sourcesIncomplete') }),
    });
  }
  const searchable = knowledge.apps.length + knowledge.collections.length;
  if (searchable) sent.push({ icon: 'travel_explore', text: t('ui.brief.sent.knowledge', { count: searchable }) });
  if (skills.length) sent.push({ icon: 'school', text: t('ui.brief.sent.skills', { skills: list(skills.map((k) => toolLabel(k))) }) });
  if (continuesRunId) sent.push({ icon: 'redo', text: t('ui.brief.sent.continues') });
  if (!sent.length) sent.push({ icon: 'check', text: t('ui.brief.sent.taskOnly') });

  const withheld: BriefRow[] = brief.withheld.map((w) => ({
    icon: WITHHELD_ICON[w.what] ?? 'block',
    text: t(`ui.brief.withheld.${w.what}`),
    note: w.items?.length
      ? t('ui.brief.reasonWithItems', { reason: t(`ui.brief.reason.${w.reason}`), items: list(w.what === 'tools' || w.what === 'skills' ? w.items.map((i) => toolLabel(i)) : w.items) })
      : t(`ui.brief.reason.${w.reason}`),
  }));

  const reach: BriefRow[] = [
    { icon: 'language', text: t(brief.reach.internet === 'none' ? 'ui.brief.reach.noInternet' : 'ui.brief.reach.allowedSites') },
    ...(brief.reach.sandbox ? [{ icon: 'deployed_code', text: t('ui.brief.reach.sandbox') }] : []),
    ...(brief.reach.tools.length
      ? [{ icon: 'build', text: t('ui.brief.reach.tools', { tools: list(brief.reach.tools.map((x) => `${appName(x.app)}: ${toolLabel(x.name)}`)) }) }]
      : [{ icon: 'build', text: t('ui.brief.reach.noTools') }]),
    ...(brief.reach.allowedWithoutAsking > 0 ? [{ icon: 'how_to_reg', text: t('ui.brief.reach.granted', { count: brief.reach.allowedWithoutAsking }) }] : []),
    { icon: 'key', text: t('ui.brief.reach.keys') },
  ];
  const connected: BriefRow[] = (brief.task?.sources ?? []).map((source) => ({
    icon: source.kind === 'mcp' ? 'hub' : 'apps',
    text: source.name,
    ...(source.summary ? { note: source.summary } : {}),
  }));
  return { goal: brief.goal, task: taskView(brief, t, options), sent, withheld, reach, sources: connected };
}

/** How the task view words what the server sends as codes and instants. */
export interface BriefOptions {
  /** The reader's locale (BCP 47). */
  locale?: string;
  /** Now, to say a due time without its date when it falls today. */
  now?: number;
  /** A carried call's tool, in plain words ("Searched the web", "Jira · Search issues"). */
  toolWords?: (tool: string) => string;
}

/** The lazy-toolset and skill-finding meta tools: plumbing nobody asked about, never a line of the brief. */
const META_TOOLS: ReadonlySet<string> = new Set(['list_toolsets', 'fetch_tools', 'search_tools', 'skills_list', 'skill_search']);
function bare(tool: string): string {
  const tail = tool.slice(tool.lastIndexOf('/') + 1);
  const sep = tail.lastIndexOf('__');
  return sep >= 0 ? tail.slice(sep + 2) : tail;
}

const sameDay = (a: Date, b: Date): boolean =>
  a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

function usd(amount: number, locale: string | undefined): string {
  const options: Intl.NumberFormatOptions = { style: 'currency', currency: 'USD', currencyDisplay: 'code' };
  try {
    return new Intl.NumberFormat(locale, options).format(amount);
  } catch {
    return new Intl.NumberFormat('en-US', options).format(amount);
  }
}

function carriedRows(task: NonNullable<JobBrief['task']>, t: Words, words: (tool: string) => string): BriefRow[] {
  const rows: Array<BriefRow & { count: number }> = [];
  for (const call of task.carried) {
    if (META_TOOLS.has(bare(call.tool))) continue;
    const text = call.skill ? t('jobPanel.carried.skill', { skill: toolLabel(call.skill) }) : words(call.tool);
    const note = call.failed ? t('jobPanel.carried.failed') : undefined;
    const same = rows.find((row) => row.text === text && row.note === note);
    if (same) {
      same.count += 1;
      continue;
    }
    rows.push({ icon: call.failed ? 'error' : call.skill ? 'school' : 'check', text, ...(note ? { note } : {}), count: 1 });
  }
  return rows.map(({ count, ...row }) => (count > 1 ? { ...row, text: t('jobPanel.carried.times', { text: row.text, count }) } : row));
}

/**
 * The task first, as the person reads it: the agent's notes are the job's plain-language task (they carry the names,
 * figures and constraints the work needs), the request beside them in the person's own words; a job kept before
 * the parts were (`task` null) shows its goal, which the server has cut to the request.
 */
export function taskView(brief: JobBrief, t: Words, options: BriefOptions = {}): BriefTaskView {
  // An older server sends no `task`: the goal is all there is.
  const task = brief.task ?? null;
  if (task === null || brief.goal === null) {
    return { text: brief.goal, asked: null, limits: [], carried: [], earlier: null };
  }
  const request = task.request.trim();
  const notes = task.notes.trim();
  const text = notes || request || brief.goal;
  const limits: string[] = [];
  if (task.dueAt) {
    const due = new Date(task.dueAt);
    const today = sameDay(due, new Date(options.now ?? Date.now()));
    const when = today ? formatTime(due, options.locale) : formatDateTime(due, options.locale);
    if (when) limits.push(t('jobPanel.limits.due', { when }));
  }
  if (task.maxCostUsd !== null) limits.push(t('jobPanel.limits.cost', { amount: usd(task.maxCostUsd, options.locale) }));
  if (task.fileName) limits.push(t('jobPanel.limits.file', { name: task.fileName }));
  return {
    text,
    asked: notes && request && request !== notes ? request : null,
    limits,
    carried: carriedRows(task, t, options.toolWords ?? ((tool) => toolLabel(tool))),
    earlier: task.earlier.trim() || null,
  };
}

/** "It started as", then "How it changed" (design §6B.42; `GET …/brief/history`, ADR-UI-01 D5), oldest first. */
export interface BriefHistoryView {
  startedAs: string | null;
  changes: BriefRow[];
}

export function briefHistoryView(history: JobBriefHistory, t: Words): BriefHistoryView {
  const changes = history.changes.map((c): BriefRow => {
    switch (c.kind) {
      case 'message':
        return { icon: 'chat', text: t('ui.brief.change.message', { text: c.text }) };
      case 'choice':
        return {
          icon: c.decision === 'approved' ? 'check_circle' : 'block',
          text: t(c.decision === 'approved' ? 'ui.brief.change.approved' : 'ui.brief.change.denied', { action: stepWords(c, t) }),
        };
      default:
        return { icon: 'redo', text: c.goal ? t('ui.brief.change.replanned', { goal: c.goal }) : t('ui.brief.change.replannedPlain') };
    }
  });
  return { startedAs: history.startedAs, changes };
}
