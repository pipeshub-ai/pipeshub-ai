/**
 * View-model shapes for the background-agent screens (jobs, the trail, the Inbox, Needs you), first drawn in the design
 * prototype (lr-pipeshub). Screens render THESE; the adapters in this folder turn API data and live frames into them,
 * so a screen never reads an API type directly.
 *
 * Sources, by type:
 *   TrailEntry, TrailEntryKind, TrailEvent   components/chat/thinking-trail-model.ts
 *   RunSegment                               components/chat/run-segments.ts
 *   AnswerBlock, AnswerSegment               components/chat/streaming-answer.tsx
 *   SandboxWorker, SandboxStep, StepKind     components/sandbox/sandbox-script.ts
 *   WorkerState, WorkerStatus, WorkerNote    components/sandbox/sandbox-store.ts
 *   InboxNotification                        components/inbox/notifications.ts
 *
 * Differences from the prototype are marked "Product:". They are additions a real backend needs (an entry's live state, an id the API knows), never a change of meaning.
 */


/* ---------------- The answer ---------------- */

export type AnswerSegment =
  /** `strong`: one or two key facts in a reply, at weight 500 (design.md §6.3). */
  | { kind: 'text'; text: string; strong?: boolean }
  | { kind: 'citation'; label: string; count?: number }
  /** Product: a link a real answer carries (the prototype's authored answers have none). */
  | { kind: 'link'; text: string; href: string }
  /** Product: an inline code span. */
  | { kind: 'code'; text: string };

export type AnswerBlock =
  | { kind: 'paragraph'; segments: AnswerSegment[] }
  | { kind: 'heading'; text: string }
  | { kind: 'title'; text: string }
  | { kind: 'section-header'; index: number; text: string }
  /** Product: `index` on a numbered item; absent on a plain bullet. `level`: how deep a nested item sits (1 under a top item). */
  | { kind: 'bullet'; segments: AnswerSegment[]; index?: number; level?: number }
  /** Product: a fenced code block (real answers carry them; the prototype's never do). */
  | { kind: 'code'; language: string; text: string }
  /** Product: a table, its cells as segments; `align` per column as the rule row says (`---:` is a number column). */
  | { kind: 'table'; header: AnswerSegment[][]; rows: AnswerSegment[][][]; align?: Array<'left' | 'right' | 'center'> };

/* ---------------- The trail (AG-UI's vocabulary) ---------------- */

export type TrailEntryKind =
  /** A named phase of the run. AG-UI StepStarted / StepFinished. */
  | 'step'
  /** The agent's own reasoning. AG-UI ReasoningMessage*. */
  | 'reasoning'
  /** A tool call. AG-UI ToolCallStart / Args. */
  | 'tool'
  /** What a tool returned. AG-UI ToolCallResult. */
  | 'tool-result'
  /** A change to shared run state. AG-UI StateDelta. */
  | 'state'
  /** A question the run put to the person, and what they answered (one entry: asked and answered is one event). */
  | 'decision';

export type TrailEvent =
  | 'StepStarted'
  | 'ReasoningMessageContent'
  | 'ToolCallStart'
  | 'ToolCallResult'
  | 'StateDelta'
  | 'UserDecision';

/** Product: where a live entry is. The prototype's scripted trail only ever shows finished rows. */
export type TrailEntryState = 'running' | 'done' | 'failed' | 'waiting';

export interface TrailEntry {
  id: string;
  kind: TrailEntryKind;
  event: TrailEvent;
  /** One line: what this step is, in plain words. Never an id or raw JSON. */
  title: string;
  /** Second line, only where it adds something. */
  detail?: string;
  /** Material Symbols glyph. */
  icon: string;
  /** How long it took, in ms; 0 while unknown. */
  ms: number;
  sourceIndex?: number;
  /** On a `decision`: what the person answered. Absent while the question is open. */
  answer?: string;
  /** Product: on a question set's row, each question with what the person answered, folded under the row. */
  answers?: ReadonlyArray<{ question: string; answer: string }>;
  /** 1 puts the entry under the narration above it. */
  depth?: 0 | 1;
  /** Product: live state. */
  state?: TrailEntryState;
}

/** A run told as a conversation: say what you are about to do, do it, say what you found (run-segments.ts). */
export interface RunSegment {
  id: string;
  label?: string;
  /** What the agent says before this segment's work. */
  intro?: ReadonlyArray<AnswerBlock>;
  /** The work. Omitted on the final segment, which is only prose. */
  trail?: ReadonlyArray<TrailEntry>;
  /** A beat in a flow rather than the reply: stays undressed (no answer toolbar). */
  step?: boolean;
}

/* ---------------- Jobs (background agents in sandboxes) ---------------- */

/** Product: the engine names the harness runs, in the person's words. The prototype's four, plus "Background agent". */
export type SandboxAgent =
  | 'Claude Code'
  | 'Codex'
  | 'Claude'
  | 'ChatGPT agent'
  | 'OpenCode'
  | 'Gemini'
  | 'Goose'
  | 'Qwen'
  | 'DeepSeek'
  | 'Background agent';

export type BriefSource = 'confluence' | 'jira' | 'slack' | 'github' | 'sheets';

export interface SandboxBrief {
  task: string;
  sent: ReadonlyArray<{ name: string; source: BriefSource; note?: string }>;
  withheld: ReadonlyArray<string>;
  reach: string;
}

export type StepKind =
  | 'boot'
  | 'command'
  | 'file'
  | 'check'
  | 'browser'
  | 'review'
  | 'signin'
  | 'approval'
  | 'handoff'
  | 'message'
  | 'fail'
  | 'retry'
  | 'restart';

export interface StepOpened {
  kind: 'web' | 'doc' | 'email';
  title: string;
  detail: string;
}

export interface SandboxStep {
  id: string;
  kind: StepKind;
  /** Plain English: what a non-engineer reads. */
  title: string;
  /** What the step says while it runs, when that differs. */
  activeTitle?: string;
  /** The exact command, for `command` and `check`. */
  command?: string;
  /** What it printed. Collapsed until asked for. */
  output?: string;
  /** Product: how a test run came out ("1 failed", "All passed"), from its output or exit code. */
  result?: string;
  /** The file it produced, for `file`. */
  file?: string;
  url?: string;
  /** The question, for `approval`. */
  question?: string;
  approve?: string;
  decline?: string;
  error?: string;
  opened?: ReadonlyArray<StepOpened>;
  /** Product: an ask's record once it was settled ("Approved by you: create an issue in Jira"). */
  stamp?: string;
  /** How long it took, in ms; 0 while running or parked. */
  ms: number;
}

export interface SandboxWorker {
  /** Product: the run id (the prototype's ids are script names). */
  id: string;
  /** What the job is, in two to four words. */
  title: string;
  agent: SandboxAgent;
  /** Product: optional until the brief read lands (plan gap G4). */
  brief?: SandboxBrief;
  steps: ReadonlyArray<SandboxStep>;
  /** The files it handed back. Product: a list (a real job can leave several). */
  outputs: ReadonlyArray<{ name: string; artifactId?: string }>;
  /** What it says when it reports back. */
  report: ReadonlyArray<AnswerBlock>;
}

export type WorkerStatus = 'queued' | 'working' | 'blocked' | 'waiting' | 'done' | 'stopped' | 'failed';

export interface WorkerNote {
  id: string;
  text: string;
  atStep: number;
}

export interface WorkerState {
  /** Index of the step in flight; equals steps.length when finished. */
  done: number;
  startedAt: number;
  finishedAt?: number;
  stopped?: boolean;
  queued?: boolean;
  blocked?: boolean;
  failed?: boolean;
  notes: ReadonlyArray<WorkerNote>;
  /** Answers to `approval` steps, by step id. */
  answers: Readonly<Record<string, 'yes' | 'no'>>;
  skipped: ReadonlyArray<string>;
  seen?: boolean;
}

/* ---------------- Inbox ---------------- */

export type NotificationSeverity = 'error' | 'success' | 'warning' | 'info';

export interface InboxNotification {
  id: string;
  severity: NotificationSeverity;
  title: string;
  description: string;
  /** Formatted relative time. */
  time: string;
  unread: boolean;
  action?: string;
  workflow?: string;
  runId?: string;
  mention?: { chatId: string; chatTitle: string; fromId: string };
  invite?: { kind: 'workflow' | 'chat'; name: string; from: string; unconfigured?: number };
  /** Product: the feed's kind (the contract's `InboxItemKind`); the prototype tells kinds apart by which field is set. */
  kind?: InboxItemKindView;
  /** Product: an ask waiting on you (approval, question, skill proposal, memory review): first, on the accent ground. */
  waiting?: boolean;
  /** Product: in the Archived view. */
  archived?: boolean;
  /** Product: the mark of the kind of thing it is about (design.md §6B.25), and its tone. */
  icon?: string;
  tone?: 'accent' | 'danger' | 'warning' | 'quiet';
  /** Product: where following the row goes, an in-app path. */
  href?: string;
  /** Product: the row's actions in weight order (§6B.23). */
  actions?: ReadonlyArray<InboxRowAction>;
  /** Product: a workflow notice's schedule, which Mute workflow names. */
  muteId?: string;
  muted?: boolean;
}

export type InboxItemKindView =
  | 'approval'
  | 'question'
  | 'skill_proposal'
  | 'artefact_review'
  | 'memory_review'
  | 'run_finished'
  | 'run_not_finished'
  | 'schedule_ran'
  | 'schedule_failed'
  | 'schedule_waiting'
  | 'share'
  | 'mention'
  | 'connector'
  | 'system';

/** Product: one action on an Inbox row. `leaves`: it follows a pointer, so the panel closes. */
export interface InboxRowAction {
  id: 'openAsk' | 'open' | 'viewRun' | 'approveSkill' | 'editSkill' | 'rejectSkill' | 'muteWorkflow' | 'unmuteWorkflow' | 'restore';
  label: string;
  weight: 'primary' | 'secondary' | 'no';
  leaves: boolean;
}

/* ---------------- Needs you ---------------- */

/** What a "Needs you" card asks for. */
export type NeedsYouKind = 'approval' | 'question' | 'signin' | 'connect';

export interface NeedsYouAction {
  id: string;
  label: string;
  tone: 'primary' | 'secondary' | 'quiet';
}

/** One question of a question card. */
export interface NeedsYouQuestion {
  id: string;
  text: string;
  /** "Pick one" or "Pick any". */
  multi: boolean;
  options: ReadonlyArray<{ id: string; label: string; detail?: string; freeText?: boolean }>;
}

/**
 * Product: an open ask, as the floating card shows it (design `needs-you-card.tsx`): what is needed in one line,
 * the plain reason in one more, details under "Why?", and the actions that DO it, the action named against the safe
 * alternative.
 */
export interface NeedsYouAsk {
  /** Stable across renders and decisions. */
  key: string;
  kind: NeedsYouKind;
  title: string;
  /** One plain line: why it asks. */
  reason?: string;
  /** Where it goes, its own line only when the title does not say it ("Sends to jira.acme.com"). */
  detail?: string;
  /** Under "Why?": the rule, what was read, where it goes. */
  why: ReadonlyArray<string>;
  /** Who asks, when it is not PipesHub itself ("Codex wants to:"). */
  from?: string;
  /** How many calls the card decides together (the N of "Allow all N"). */
  count: number;
  actions: ReadonlyArray<NeedsYouAction>;
  questions?: ReadonlyArray<NeedsYouQuestion>;
  /** Product: a question set's answers so far, as the run has them ("3 of 6 answered"). */
  progress?: { answered: number; total: number };
  /** A decision is on its way. */
  sending?: boolean;
  /** The server refused the last decision; the card says so and stays. */
  error?: string;
  /** Where a sign-in or connect opens, when it is a page. */
  href?: string;
  /** Product: the job that asks, when a background agent does: the card names it and its step, with See activity. */
  jobRunId?: string;
}
