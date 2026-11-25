/**
 * A job's brief (ADR-UI-01 D3, gap G4): what a job (an engine or sub-agent child run) was told, what it was sent and
 * what was kept from it and why, and how far it can reach, in plain fields: no spec, digest or policy layer. Pure:
 * built from the child's pinned spec and input and its parent's pinned spec, all fixed at the child's admission, so
 * the brief never changes after the job starts.
 */
import type { RunSpec } from '../contracts/generated/core/run-spec';
import type { HarnessRun, RunInputSnapshot, RunTaskParts } from './execution';
import { pipeshub } from './child-runs';

/** What runs the job: a coding engine in a sandbox, or a sub-agent on the native loop. */
export type BriefAgent =
  | { kind: 'engine'; engine: string }
  | { kind: 'sub_agent'; role: string | null };

/** One source the parent had read when it handed the work over, as its context label names it. */
export type BriefSource =
  | { kind: 'record'; recordId: string; attachment: boolean }
  | { kind: 'connector_item'; connector: string; externalRecordId: string };

/**
 * What a job was not given, and why:
 * - `conversation` (`task_only`): it gets its task, never the chat around it;
 * - `credentials` (`outside_sandbox`): keys and sign-ins stay outside its sandbox; calls are made for it;
 * - `tools`, `knowledge`, `skills` (`not_granted`): the parent's own it was not handed;
 * - `internet` (`not_granted`): the parent may reach websites, the job may not;
 * - `files` (`not_handed_off`): files of the conversation it was not given.
 */
export type WithheldWhat =
  | 'conversation'
  | 'credentials'
  | 'tools'
  | 'knowledge'
  | 'skills'
  | 'internet'
  | 'files';
export type WithheldReason =
  'task_only' | 'outside_sandbox' | 'not_granted' | 'not_handed_off';

export interface BriefWithheld {
  what: WithheldWhat;
  reason: WithheldReason;
  /** The items, where they have plain names (tools by name, knowledge by id); absent for a whole category. */
  items?: string[];
}

export interface BriefTool {
  toolId: string;
  /** The app the tool belongs to (`jira`), as its id names it. */
  app: string;
  /** The tool's own name (`search_issues`). */
  name: string;
}

export interface JobBrief {
  runId: string;
  parentRunId: string;
  sessionId: string;
  agent: BriefAgent;
  /** The task it was given, in the parent's words; null once the conversation's content was erased. */
  goal: string | null;
  /**
   * Its task in its parts, as the harness composed it (BGUI-10): the request, the chat agent's notes, its limits and
   * the calls it started from, by tool alone. Null for a job admitted before the parts were kept, or once erased.
   */
  task: RunTaskParts | null;
  sent: {
    /** Files of the turn handed to it (an engine job's input artifacts). */
    files: Array<{ artifactId: string; version: number | null }>;
    /** What the parent had read when it handed the work over: the job's task may carry it. */
    sources: BriefSource[];
    /** False: some of what the parent had read cannot be named (content of unknown origin). */
    sourcesComplete: boolean;
    /** What it may search itself: connector apps and collections, by id. */
    knowledge: { apps: string[]; collections: string[] };
    skills: string[];
    /** It continues an earlier job of the conversation from where that one left off. */
    continuesRunId: string | null;
  };
  withheld: BriefWithheld[];
  reach: {
    /** `none`: no website; `allowed_sites`: only the websites the org allows. */
    internet: 'none' | 'allowed_sites';
    /** It works in a private sandbox (files, commands). */
    sandbox: boolean;
    tools: BriefTool[];
    /** Conversation grants it started with: tool and destination pairs it may use without asking. */
    allowedWithoutAsking: number;
  };
}

/** Longest job title a list shows (code points). */
export const MAX_JOB_TITLE = 200;

/**
 * What a sub-agent's goal opens with when its coordinator gave it a part of the request: the person's request,
 * quoted for context, then this header and its own task (`harness_core/kernel/agent/user_request.py`
 * `_DELEGATED_TASK_HEADER`; `tests/.../bgui8-delegated-task.test.ts` keeps the two the same).
 */
export const DELEGATED_TASK_HEADER = '## Your delegated task';

/**
 * A job's own task, as a person reads it: the part of its goal after the delegated-task header when it has one.
 * Read whole, the goal put the quoted request and both headers in markdown at the top of the job's Brief, and its
 * title on the Jobs page read "## The user's request (verbatim, …)" (BGUI-8, a deep turn that split its work).
 */
export function delegatedTask(goal: string | null | undefined): string | null {
  if (typeof goal !== 'string') return null;
  const at = goal.lastIndexOf(DELEGATED_TASK_HEADER);
  if (at < 0) return goal;
  const task = goal.slice(at + DELEGATED_TASK_HEADER.length).trim();
  return task.length > 0 ? task : goal;
}

/**
 * The sections `TaskSpec.render` (`app/harness/assembly/task_spec.py`) adds after the person's request: the file to
 * deliver, the budget line, the chat agent's notes, the carried calls, the connected sources (ADR-OWD-07 D-DISC-2)
 * and the skills the chat chose (R31HOT2). `bgui10-job-task.test.ts` keeps them the same as Python's.
 */
export const TASK_SPEC_SECTIONS: readonly string[] = [
  'The file to deliver: ',
  "This task's time and money: ",
  'Notes from the chat agent that handed this task to you:',
  'Tool calls this chat already made before handing over, with their results.',
  'Connected sources this task can reach, besides company knowledge:',
  'Skills the chat agent chose for this task (installed for you, read each one first): ',
];

/**
 * An engine job's goal from before its task's parts were kept (BGUI-10), for its history only: the person's request
 * that opens it, without the sections the harness added for the engine (the budget sentence, the notes heading and
 * the carried calls' raw results). A goal without them is its own.
 */
export function legacyEngineGoal(goal: string | null): string | null {
  if (goal === null) return null;
  const cuts = TASK_SPEC_SECTIONS.map((heading) =>
    goal.indexOf(`\n\n${heading}`),
  ).filter((at) => at > 0);
  if (cuts.length === 0) return goal;
  const request = goal.slice(0, Math.min(...cuts)).trim();
  return request.length > 0 ? request : goal;
}

/**
 * A job's title (UI-B5, UIP2-FU-1): the first line of the goal its parent gave it, its spaces folded, cut at
 * `MAX_JOB_TITLE` code points with an ellipsis; absent for no goal (erased, or a run that is no job).
 */
export function jobTitle(goal: string | null | undefined): string | undefined {
  const line = (delegatedTask(goal) ?? '')
    .split(/\r?\n/)
    .map((text) => text.replace(/\s+/g, ' ').trim())
    .find((text) => text.length > 0);
  if (line === undefined) return undefined;
  const points = Array.from(line);
  return points.length <= MAX_JOB_TITLE
    ? line
    : `${points
        .slice(0, MAX_JOB_TITLE - 1)
        .join('')
        .trimEnd()}…`;
}

/** `app@instance/name` as its app and name; an id of another shape is its own name. */
export function briefTool(toolId: string): BriefTool {
  const slash = toolId.lastIndexOf('/');
  const at = toolId.indexOf('@');
  return {
    toolId,
    app:
      at > 0
        ? toolId.slice(0, at)
        : slash > 0
          ? toolId.slice(0, slash)
          : toolId,
    name: slash >= 0 ? toolId.slice(slash + 1) : toolId,
  };
}

const sorted = (values: Iterable<string>): string[] =>
  [...new Set(values)].sort();
const minus = (held: readonly string[], given: readonly string[]): string[] => {
  const own = new Set(given);
  return sorted(held.filter((item) => !own.has(item)));
};

function briefSources(spec: RunSpec): {
  sources: BriefSource[];
  complete: boolean;
} {
  const label = pipeshub(spec)?.inheritedLabel;
  if (label === undefined) return { sources: [], complete: true };
  const sources = label.sources.flatMap((source): BriefSource[] => {
    const raw = source as Record<string, unknown>;
    if (typeof raw['recordId'] === 'string') {
      return [
        {
          kind: 'record',
          recordId: raw['recordId'],
          attachment: raw['kind'] === 'attachment',
        },
      ];
    }
    if (
      typeof raw['connector'] === 'string' &&
      typeof raw['externalRecordId'] === 'string'
    ) {
      return [
        {
          kind: 'connector_item',
          connector: raw['connector'],
          externalRecordId: raw['externalRecordId'],
        },
      ];
    }
    return [];
  });
  return { sources, complete: label.complete };
}

/**
 * The brief of `child`, a job, from its pinned spec, its input (null once erased) and its parent's pinned spec
 * (null when the parent's pin cannot be read: then nothing is said withheld that only the parent's grants show).
 */
export function jobBrief(
  child: Pick<HarnessRun, 'runId' | 'sessionId' | 'parent'> & {
    input?: RunInputSnapshot | null;
  },
  childSpec: RunSpec,
  parentSpec: RunSpec | null,
  parentInput: RunInputSnapshot | null,
): JobBrief {
  const own = pipeshub(childSpec);
  const parentOwn = parentSpec === null ? undefined : pipeshub(parentSpec);
  const ownKnowledge = own?.knowledge;
  const parentKnowledge = parentOwn?.knowledge;
  const executor = childSpec.executor;
  const engine = executor?.kind === 'engine' ? executor.engine.kind : null;
  const agent: BriefAgent =
    engine !== null
      ? { kind: 'engine', engine }
      : { kind: 'sub_agent', role: own?.subAgent?.role ?? null };
  const tools = sorted(childSpec.grants.tools.map((grant) => grant.toolId));
  const knowledge = {
    apps: sorted(ownKnowledge?.apps ?? []),
    collections: sorted(ownKnowledge?.kb ?? []),
  };
  const skills = sorted((own?.skills ?? []).map((skill) => skill.name));
  const files = (child.input?.inputArtifacts ?? []).map((artifact) => ({
    artifactId: artifact.artifactId,
    version: artifact.version ?? null,
  }));
  const { sources, complete } = briefSources(childSpec);
  const goal = delegatedTask(child.input?.query);
  // An erased input erases its parts with it: no part outlives the goal.
  const task = goal === null ? null : (child.input?.task ?? null);

  const withheld: BriefWithheld[] = [
    { what: 'conversation', reason: 'task_only' },
  ];
  if (engine !== null)
    withheld.push({ what: 'credentials', reason: 'outside_sandbox' });
  if (parentSpec !== null) {
    const keptTools = minus(
      parentSpec.grants.tools.map((grant) => grant.toolId),
      tools,
    ).map((toolId) => briefTool(toolId).name);
    if (keptTools.length > 0) {
      withheld.push({
        what: 'tools',
        reason: 'not_granted',
        items: sorted(keptTools),
      });
    }
    const keptKnowledge = [
      ...minus(parentKnowledge?.apps ?? [], knowledge.apps),
      ...minus(parentKnowledge?.kb ?? [], knowledge.collections),
    ];
    if (keptKnowledge.length > 0) {
      withheld.push({
        what: 'knowledge',
        reason: 'not_granted',
        items: sorted(keptKnowledge),
      });
    }
    const keptSkills = minus(
      (parentOwn?.skills ?? []).map((skill) => skill.name),
      skills,
    );
    if (keptSkills.length > 0) {
      withheld.push({
        what: 'skills',
        reason: 'not_granted',
        items: keptSkills,
      });
    }
    if (
      parentSpec.grants.egress !== 'none' &&
      childSpec.grants.egress === 'none'
    ) {
      withheld.push({ what: 'internet', reason: 'not_granted' });
    }
  }
  const conversationFiles =
    (parentInput?.attachments.length ?? 0) +
    (parentInput?.inputArtifacts?.length ?? 0);
  if (conversationFiles > files.length) {
    withheld.push({ what: 'files', reason: 'not_handed_off' });
  }

  return {
    runId: child.runId,
    parentRunId: child.parent?.runId ?? '',
    sessionId: child.sessionId,
    agent,
    goal: task === null && engine !== null ? legacyEngineGoal(goal) : goal,
    task,
    sent: {
      files,
      sources,
      sourcesComplete: complete,
      knowledge,
      skills,
      continuesRunId: own?.engineCarry?.runId ?? null,
    },
    withheld,
    reach: {
      internet: childSpec.grants.egress === 'none' ? 'none' : 'allowed_sites',
      sandbox: engine !== null || childSpec.grants.sandboxProfile !== 'none',
      tools: tools.map(briefTool),
      allowedWithoutAsking: own?.inheritedGrants?.length ?? 0,
    },
  };
}
