import {
  OPERATOR_REASONS,
  OPERATOR_SCOPE_KINDS,
  type OperatorStop,
} from '../domain/operator-controls';
import { runDisplay, type RunDisplay } from '../domain/run-policy-fields';
import {
  CUT_SHORT_REASONS,
  TOOL_ERROR_CODES,
  type CutShortReason,
  type ToolErrorCode,
} from '../contracts/generated/core/enums';
import type { RuleLayer } from '../contracts/generated/core/enums';
import {
  APPROVAL_COUNTED,
  CONNECT_PREFIX,
  ruleLayer,
} from '../domain/interactions';
import { JournalEvent } from '../domain/journal';
import { displaySafe } from '../domain/display-safe';
import { jobTitle } from '../domain/job-brief';
import type { AdmittedRunKind } from '../domain/run-kind';
import { LegacyEngineLines } from './legacy-engine-lines';
import { ORG_DEFAULT_MODEL } from '../domain/run-spec';
import { MAX_DENY_REASON_CHARS } from '../domain/execution';
import { outcomeView, type OutcomeView } from './outcome-view';
import {
  runGatesSchema,
  verificationOf,
  type RunVerification,
} from '../domain/run-gates';
import {
  SITE_UPLOAD_TOOL,
  allowedSiteLine,
  blockedSiteLine,
  siteUploadDecisionLine,
  type SiteAccessShown,
  type SiteActivityView,
  type SiteUse,
} from '../domain/site-activity';
import {
  REPOSITORY_FETCHED,
  REPOSITORY_UNAVAILABLE,
  unavailableRepository,
  type RepositoryUnavailableView,
  REPOSITORY_FETCH_TOOL,
  REPOSITORY_PUSH_TOOL,
  deniedRepositoryLine,
  fetchedRepositoryLine,
  isRepositoryTool,
} from '../domain/repository-activity';
import {
  SECRET_HIDDEN,
  withSecretHidden,
  type SecretsHiddenView,
} from '../domain/secret-activity';
import { codePoints } from './text-filter';
import { isBudgetScope, type BudgetScope } from '../domain/cost-ledger';
import {
  pipeshubCouldNotDoSchema,
  type PipeshubCouldNotDo,
} from '../contracts/generated/ext/pipeshub/could-not-do';
import {
  pipeshubToolStepSchema,
  type PipeshubToolStep,
} from '../contracts/generated/ext/pipeshub/tool-step';
import { isSharedGrant } from '../domain/conversation-grants';

/**
 * A run's journal folded into what the stream and the transcript show
 * (REPORT 6.1.6: projections are rebuilt from the journal). Pure; the frame
 * projector and the transcript projector both read it, so the persisted
 * `parts[]` equal the live ones by construction (L-6).
 */

export interface CallView {
  callId: string;
  name: string;
  turn: number;
  index: number;
  argsJson: string;
  /** The bounded serialization, when it differs from `argsJson` (`serialize_tool_args`): read it through `boundedArgs`. */
  args?: string;
  /**
   * What the saved part keeps where it differs from what the stream showed: this request's citation refs as the
   * URLs they stand for (legacy's saved tool part). Read through `savedArgs`; the live frames never show it.
   */
  saved?: { args?: string; content?: string; resultSummary?: string };
  argsSummary?: string;
  displayName?: string;
  hidden: boolean;
  /** Its arguments streamed as the answer (`final_answer`), not as a tool call. */
  streamsAnswer?: boolean;
  status?: 'completed' | 'failed' | 'blocked';
  /** The `tool.denied` rule of a blocked call: policy, interaction_denied, flow_external_write. */
  blockedBy?: string;
  outcome?: string;
  content?: string;
  resultSummary?: string;
  artifactId?: string;
  /** The tool's small view of the full result (`Tool.result_view`), as the worker admitted it. */
  resultView?: Record<string, unknown>;
  /** Which page, query, pattern or file the call used, and what came of it (WP-115), as the worker built it. */
  step?: PipeshubToolStep;
  /** A failure's code: what the person reads instead of its detail (ERR-UX). */
  errorCode?: ToolErrorCode;
  /** The redacted detail of a coded failure: the model's text, the owner's activity panel only. */
  detail?: string;
  /** The call's intent was a write or an execute (`tool.intent` effect). */
  writes?: boolean;
  /** A dry run's call (G-03): recorded as what the run would have done, never made; whether it would have asked. */
  shadow?: { wouldAsk: boolean };
  /** The plan step in progress when the model made the call (UXR-B1). */
  stepId?: string;
  /** Journal time of the response that made the call and of its outcome (CTL-3b). */
  startedAt?: string;
  endedAt?: string;
}

/** A call's arguments as the live stream previews them (legacy's `argsPreview`): bounded, refs as the model wrote them. */
export const boundedArgs = (call: CallView): string =>
  call.args ?? call.argsJson;

/** A call's arguments as its saved part keeps them. */
export const savedArgs = (call: CallView): string =>
  call.saved?.args ?? boundedArgs(call);

export interface TurnView {
  turn: number;
  fence: number;
  modelAttempt: number;
  text: string;
  /** The response's reasoning, as the stream showed it. */
  reasoning?: string;
  /** Where in `text` (code points) the answer a tool call streamed begins. */
  answerFrom?: number;
  /** Cut off at the output-token limit; the next text-only reply continues it. */
  truncated?: boolean;
  /**
   * A turn the run's director took itself, with no model call (the kernel's `is_synthetic`): the worker's finalizer
   * counts only model turns in `partTexts`, so the transcript opens no text part for it.
   */
  synthetic?: boolean;
  calls: CallView[];
  /** The plan step in progress when the model answered (UXR-B1). */
  stepId?: string;
  /** Journal time the turn's model call began (the step before it ended) and when it answered (CTL-3b). */
  startedAt?: string;
  respondedAt?: string;
}

export type TaskStatus = 'pending' | 'in_progress' | 'completed' | 'cancelled';

/** One step of the run's plan (`task.*`, UXR-B1): whichever executor stated it. */
/** One `x.pipeshub.run.notice`: its code, and the item it names (QA23-F1 `credential_missing`). */
export interface NoticeView {
  code: string;
  item?: string;
  kind?: string;
  /** `engine_switched` (WP-111): the coding engine the turn's code work ran on instead of the one that is off. */
  engine?: string;
  /** `mcp_unavailable` (MCPNOTE-1): why the server in `item` was left out, and the one to sign in to from the chat. */
  reason?: string;
  instance?: string;
  /** `mcp_tool_held` (D-MCP-5): the tools of the server in `item` the description scanner held back. */
  tools?: string[];
  /** `verified` / `unverified` (G-05): every check of the run's last verification, for the answer's panel. */
  checks?: RunVerification['checks'];
  /** The verification round that settled it: 2 when the run was sent back once to fix a failed check. */
  round?: number;
  /** `time_plan` (ADR-RUN-01 D4): when the run starts writing at the latest, and when its work must be finished. */
  writeBy?: string;
  finishBy?: string;
  /**
   * WP-89: which time a state notice the run moves in and out of was journaled (1, 2, …): a second retire after a
   * resume is shown again, where every other notice shows once.
   */
  occurrence?: number;
}

export interface TaskView {
  taskId: string;
  title: string;
  status: TaskStatus;
  deps: string[];
  /** The step names a tool the run's rules ask about; absent: not known. */
  asksFirst?: boolean;
}

export interface QuestionView {
  callId: string;
  status: string;
  toolData: unknown;
}

/** Tools a `search_tools` call found that the agent cannot call (legacy `tool_unavailable`). */
export interface UnavailableView {
  callId: string;
  hits: Array<Record<string, string>>;
}

export interface ArtifactView {
  callId?: string;
  artifactId: string;
  fileName: string;
  mimeType: string;
  sizeBytes: number;
  artifactType: string;
  isTemporary: boolean;
  recordId?: string;
  documentId?: string;
  version?: number;
  derivedFromCodeArtifactId?: string;
  visibility: string;
  card: boolean;
  /** P3.5: a draft is presented again as final or discarded; legacy presentations have none. */
  lifecycle?: ArtifactLifecycle;
}

export type ArtifactLifecycle = 'draft' | 'final' | 'discarded';

/** The notice a run's time plan is shown with (ADR-RUN-01 D4). */
/** A director's own turn as the kernel journals it (`harness_core/kernel/durable/director.py`). */
const SYNTHETIC_PROVIDER = 'director';
const SYNTHETIC_TRANSPORT = 'synthetic';

export const TIME_PLAN = 'time_plan';

/** G-48: the degradation chain's notices Node derives from core events (WP-89). */
export const MODEL_SWITCHED = 'model_switched';
export const SANDBOX_QUEUED = 'sandbox_queued';

/** WP-89: notices of a state the run moves in and out of (ADR-OPS-01 D6), shown each time they are journaled. */
export const REPEATING_NOTICES: ReadonlySet<string> = new Set([
  'engine_retired',
  'engine_continued',
]);

const LIFECYCLES: ReadonlySet<string> = new Set([
  'draft',
  'final',
  'discarded',
]);

/**
 * A message the user sent while the run was active (steering, T2): where the
 * run consumed it, counted in model turns before it; null while it waits.
 */
export interface InputView {
  messageSeq: number;
  text: string;
  afterTurns: number | null;
  /** The files it named by reference (C-21), as the chat request named them. */
  attachments?: Array<Record<string, unknown>>;
}

export interface AnswerRecord {
  outcome: 'answered' | 'needs_input' | 'stopped' | 'error';
  answer: string;
  partTexts: string[];
  citations: unknown[];
  confidence: string | null;
  answerMatchType?: string;
  errorCode?: string;
  /** Legacy `completion.reasoning`: `{turnIndex, content}` per turn that reasoned. */
  reasoning?: Array<Record<string, unknown>>;
  /** The MCP servers main's sign-in card lists (FU4), on a run started in the web app. */
  mcpSignIn?: Array<Record<string, unknown>>;
  /** Which limit cut the run's work short: the answer is what it had gathered (BUDGETEND). */
  truncatedBy?: CutShortReason;
  /** With `truncatedBy` `budget`: which shared budget ran out (C9.06, WP-51), opaque here; absent: the run's own limit. */
  budgetScope?: BudgetScope;
  /** WP-89: the settled outcome the chat words; the answer then holds only the model's prose. */
  settled?: OutcomeView;
  /** ANSWER-CONTRACT-FU-2: the subject was not found; the web chat words it, the answer starts with `line`. */
  notFound?: NotFoundView;
  /** R32REPO-FU-1: what the run tried and could not do; the web chat words it, the answer starts with `line`. */
  limits?: CouldNotDoView;
}

/**
 * ADR-RUN-03 D4 (FU-2): the request's subject was not found in the person's knowledge. The web chat words it in the
 * reader's language above the answer and leaves out `line`, the English opening the answer starts with for a
 * surface with no UI. `near`: the closest records' names, when the person could not be asked.
 */
export interface NotFoundView {
  subject: string;
  hits: number;
  line: string;
  near?: string[];
  proceeded?: true;
  /** The connected sources searched besides the person's PipesHub knowledge, which the words name with it. */
  places?: string[];
}

/**
 * ADR-RUN-03 D5 (R32REPO-FU-1): what the run, or a job it handed work to, tried and could not do, from its journal
 * (a clone declined, refused, not allowed or failed). The web chat words the items above the answer in the reader's
 * language and leaves out `line`, the English the answer starts with (after the not-found line) for a surface with
 * no UI.
 */
export interface CouldNotDoView {
  items: PipeshubCouldNotDo[];
  line: string;
}

/** One `x.pipeshub.engine.progress` line as the `code_task` card reads it (U1, E1). */
export interface EngineFrameView {
  tool: string;
  taskId: string;
  kind: string;
  text: string;
  data: Record<string, unknown>;
  /** The plan step in progress when the engine said it (UXR-B1); `taskId` is the engine's own task. */
  stepId?: string;
}

/** An engine run's task: its progress lines, kept for the card a reload rebuilds (U3). */
export interface EngineTaskView {
  taskId: string;
  frames: EngineFrameView[];
  /** Output or diff lines were left out to keep the saved conversation bounded. */
  truncated: boolean;
}

export type ApprovalStatus =
  'pending' | 'approved' | 'denied' | 'closed' | 'expired';

/** A source the run read, as the approval card names it: a title and a link, never shown as an id (UX1). */
export interface ApprovalSource {
  title?: string;
  recordId?: string;
  url?: string;
}

/**
 * Why the worker asks (`interaction.explained`): the rule and whose it is
 * (QA6-F6) and, for a rule that reads them (UX1), what the run read and where
 * the call would send it. Any other field is one an edition's rule adds
 * (`editionReasonFields`), carried as journaled.
 */
export interface ApprovalReason {
  readonly [editionField: string]: unknown;
  rule: string;
  /** Absent for a reason journaled before layers were named. */
  layer?: RuleLayer;
  /** Absent for a rule that does not read the run's context (a layer's own approval rules). */
  sensitivity?: 'public' | 'private' | 'unknown';
  sources: ApprovalSource[];
  moreSources: number;
  destination?: string;
  conversationGrant: boolean;
  /** Main's "Don't ask again" may save the answer as the person's own rule (BUGS-B). */
  ruleGrant?: true;
  /** The person the run acts for may edit the arguments before approving (AP-06). */
  editable?: true;
  /** With `editable`: the tool's argument names in its schema's order, for the edit card (WP-56). */
  fieldOrder?: string[];
}

/**
 * What a held call would change and cost (C3.71, UX-03), as the worker read it from the call's arguments and the
 * tool's declaration; carried on the live card frame, the approval view and the saved part, which the card shows.
 */
export interface ApprovalEffect {
  diff?: string;
  diffTruncated?: true;
  costEstimate?: { usd: number; basis: 'declared' };
}

/** A call held for a person's approval (P2.6c): a loop call or an engine's (E1). */
export interface ApprovalView {
  interactionId: string;
  toolCallId: string;
  toolName: string;
  displayName?: string;
  args: string;
  argsSummary?: string;
  status: ApprovalStatus;
  reason?: ApprovalReason;
  /** `conversation`: approved for the rest of the conversation (UX1). */
  scope?: 'conversation';
  /** Model turns before it was asked, as steering inputs are placed. */
  afterTurns: number;
  /**
   * How many of the run's asks were decided before this one was asked (WP-113): asks open together share it, so the
   * chat groups only calls that waited together. An engine asks one call at a time, all in one model turn.
   */
  decidedBefore: number;
  seq: number;
  /** The plan step in progress when it was asked (UXR-B1). */
  stepId?: string;
  effect?: ApprovalEffect;
  /** AP-05: the reason the person gave with a denial, so the receipt quotes it after a reload. */
  denyReason?: string;
  /** AP-06: the arguments the person approved in place of the call's own (JSON), so the receipt shows what ran. */
  editedArgs?: string;
  /** When it was asked (the request's journal time, ISO): the chat says how long it has waited (WP-97). */
  requestedAt?: string;
  /** An ask a rule says needs several approvals, some given, still waiting for the rest (counts only, never who). */
  approvals?: { counted: number; required: number };
}

/**
 * A call waiting for its run's owner to connect the item it needs (G-12):
 * `connected` once they said they connected it, `declined` or `expired`
 * when it was refused, `closed` when the run ended first.
 */
export type ConnectStatus =
  'pending' | 'connected' | 'declined' | 'expired' | 'closed';

export type ConnectItemKind = 'toolset' | 'mcp';

/** Where the person connects an item in the app (the worker's `CONNECT_PATHS`): relative, never a host or a token. */
export const CONNECT_PATHS: Readonly<Record<ConnectItemKind, string>> = {
  toolset: '/workspace/actions',
  mcp: '/workspace/mcp-servers/personal',
};

export interface ConnectView {
  interactionId: string;
  /** The loop call that waits, or the interaction's id for an engine's call. */
  toolCallId: string;
  toolName?: string;
  displayName?: string;
  item: string;
  itemKind: ConnectItemKind;
  instanceId: string;
  connectPath: string;
  /** WP-64: it was connected, and its vendor rejected the saved sign-in during the run; the person reconnects it. */
  reason?: 'sign_in_rejected';
  /** When the run asked (ISO): for `sign_in_rejected`, when the sign-in was rejected. */
  askedAt: string;
  status: ConnectStatus;
  afterTurns: number;
  seq: number;
  stepId?: string;
}

export type ChildStatus = 'running' | 'completed' | 'failed' | 'cancelled';

/** The model a run calls, as its journal names it (MA1). */
export interface ModelLabel {
  provider: string;
  model: string;
}

/** A child run this run spawned (P6.1, K-L). */
export interface ChildView {
  runId: string;
  invocationId?: string;
  taskId: string | null;
  /** Legacy `role_name`: `pipeshub-subagent-<role>` from its spawn call. */
  role: string;
  status: ChildStatus;
  errorType?: string;
  /** With `errorType` `out_of_memory`: the sandbox's memory limit in MB (R32SECCOMP-FU-4). */
  memoryLimitMb?: number;
  startedAt: string;
  updatedAt: string;
  afterTurns: number;
  seq: number;
  /** The model its role set, from `child.spawned` (MA1); absent: it calls this run's. */
  model?: ModelLabel & { role: string };
  /** CTL-6: the goal of the `code_task` call that handed this engine child its task. */
  codeTaskGoal?: string;
  /**
   * CJFIX-B: a loop sub-agent's name, its `spawn_agent` goal's first line as the Jobs list titles the same job
   * (`jobTitle`), never its role id.
   */
  goal?: string;
  /** DEEPROUTE: why the run handed this work to the engine on its own (`x.pipeshub.run.handoff`). */
  codeTaskHandoff?: CodeTaskHandoff;
}

/** DEEPROUTE: a hand-off as the chat words it: when (`upfront`, `escalation`) and why. */
export interface CodeTaskHandoff {
  trigger: string;
  reason: string;
  /** The engine kind the run pinned (`x.pipeshub.codeTask`); absent when the spec was not seen. */
  engine?: string;
}

/** The loop's handoff to an engine child (CTL-6). */
export const CODE_TASK_TOOL = 'code_task';

/** The loop's hand-off to a loop sub-agent; its `goal` names the job (CJFIX-B). */
export const SPAWN_AGENT_TOOL = 'spawn_agent';

export type RunEnd =
  | {
      status: 'completed';
      outcome: 'answered' | 'needs_input';
      /** Which limit cut the run's work short (BUDGETEND). */
      truncatedBy?: CutShortReason;
    }
  | { status: 'cancelled' }
  | {
      status: 'failed';
      errorType: string;
      message?: string;
      servers?: string[];
      /** `out_of_memory`: the sandbox's memory limit in MB (R31GVISO-FU-3). */
      memoryLimitMb?: number;
    }
  | { status: 'quarantined'; errorType: string };

export interface Timeline {
  runId: string;
  /** The goal the run's pre-run steps settled on (legacy `goal_description`). */
  goal?: string;
  turns: TurnView[];
  questions: QuestionView[];
  unavailable: UnavailableView[];
  artifacts: ArtifactView[];
  /** Steering inputs in queue order. */
  inputs: InputView[];
  answer: AnswerRecord | null;
  end: RunEnd | null;
  /** Outcomes in journal order: what `succeeded_tool_names` / `failed_tool_names` list. */
  outcomes: Array<{ name: string; ok: boolean }>;
  /** Set once the run journals an engine progress line (E1). */
  engine: EngineTaskView | null;
  approvals: ApprovalView[];
  /** Calls waiting for the run's owner to connect an item (G-12), in the order they were asked. */
  connects: ConnectView[];
  children: ChildView[];
  /** The run's plan in the order its steps were first stated (UXR-B1). */
  tasks: TaskView[];
  /** What the run tells the person beside its steps (CTL-5b), each once. */
  notices: NoticeView[];
  /** WP-89: the run's settled outcome, when its worker left the wording of it to the chat. */
  outcome?: OutcomeView;
  /** Journal time of the run's first event and of its end (CTL-3b); absent until it ends. */
  startedAt?: string;
  endedAt?: string;
  /** The step most recently moved to `in_progress` and still there; null: none. */
  currentTaskId: string | null;
  /**
   * The model the run calls (MA1): the route of its latest model request, and
   * until it has made one the first route of its pinned spec, which every
   * durable run journals (an engine's calls go through the gateway and are
   * not journaled). Absent while the pin names only the org default.
   */
  model?: ModelLabel;
  /** The /command the run's input was expanded from (SLASH1); absent: the user typed the text. */
  command?: CommandView;
  /** What the person's `/compact` did before the run's first turn (SLASH2 C2); absent: none was asked. */
  compacted?: CompactedView;
  /**
   * WP-63: the run's reach to the outside through the egress proxy, in
   * journal order: each allowed website request and each refused one, as a
   * plain line. Absent: its sandboxes reached nothing beyond the gateways.
   */
  sites?: SiteActivityView[];
  /**
   * WP-63: clones refused because their host is not on the org's list, in
   * journal order: the chat's "Clone not available" cards. Absent: none.
   */
  repositoriesUnavailable?: RepositoryUnavailableView[];
  /** WP103-FU-1: the credential shapes a gateway hid from the run's model calls, by kind. Absent: none. */
  secretsHidden?: SecretsHiddenView;
  /** The operator control a cancel came from (ADR-OPS-01); absent: the person or a parent stopped it. */
  stoppedBy?: OperatorStop;
  /** The coding engine an engine run's pinned spec names (`codex`, `claude_code`, ...); absent for a loop run. */
  engineKind?: string;
  /** What runs the turn, from the pinned spec: `loop`, or the engine's kind. Absent before the spec is pinned. */
  executor?: string;
  /** The reasoning effort the pinned spec asks for; null: the model's default. Absent before the spec is pinned. */
  reasoningEffort?: string | null;
  /**
   * What the conversation shows of the run (C4.10 `ui.reasoning`, `ui.terminal`), from the pin its spec carries:
   * a hidden part is never folded, so no projection sends it. Absent before the spec is pinned.
   */
  display?: RunDisplay;
  /** The coding engine a loop run's `code_task` hands off to (CMDUI `/status`); null: none. Absent before the pin. */
  codingEngine?: string | null;
  /** How many tools the pinned spec grants the run. Absent before the spec is pinned. */
  toolsGranted?: number;
  /** The pinned spec's run kind (`interactive`, `command`, ...). Absent before the spec is pinned. */
  runKind?: string;
  /** The run gates its spec pins (G-03..G-05); absent: none, or the spec is not pinned yet. */
  gates?: { dryRun: boolean; planApproval: boolean; verify: boolean };
  /** What the run's verify gate found last (G-05, `verification.recorded`); absent: not checked (yet). */
  verification?: RunVerification;
}

/** What the run shows of the /command it started from: never the arguments or the text, which the message holds. */
export interface CommandView {
  name: string;
  version: string;
  expandedChars: number;
  owner: string;
}

/** `x.pipeshub.context.compacted` as the chat's saved row reads it (SLASH2 C2). */
export interface CompactedView {
  executor: string;
  outcome: 'compacted' | 'nothing_to_compact' | 'unsupported' | 'failed';
  beforeTokens: number | null;
  afterTokens: number | null;
  userMessagesKept: boolean;
  focus?: string;
}

const COMPACT_OUTCOMES: ReadonlySet<string> = new Set([
  'compacted',
  'nothing_to_compact',
  'unsupported',
  'failed',
]);

const tokenCount = (value: unknown): number | null =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0
    ? value
    : null;

/** The model a child's row shows: its role's, else the model of the run that spawned it (inherited, no role). */
export const childModelLabel = (
  timeline: Pick<Timeline, 'model'>,
  child: Pick<ChildView, 'model'>,
): (ModelLabel & { role?: string }) | undefined =>
  child.model ?? timeline.model;

const modelLabel = (route: unknown): ModelLabel | undefined => {
  const { provider, model } = record(route);
  return typeof provider === 'string' &&
    provider !== '' &&
    typeof model === 'string' &&
    model !== ''
    ? { provider, model }
    : undefined;
};

export const COMMAND_RUN: AdmittedRunKind = 'command';

/** `run.cancel_requested`'s `stop`, read as the schema allows it; anything else is no stop. */
export function operatorStopOf(raw: unknown): OperatorStop | null {
  const stop = record(raw);
  const scope = stop['scope'];
  const reason = stop['reason'];
  return typeof scope === 'string' &&
    (OPERATOR_SCOPE_KINDS as readonly string[]).includes(scope) &&
    typeof reason === 'string' &&
    (OPERATOR_REASONS as readonly string[]).includes(reason)
    ? {
        scope: scope as OperatorStop['scope'],
        reason: reason as OperatorStop['reason'],
      }
    : null;
}

const RUN_ENDS: ReadonlySet<string> = new Set([
  'run.completed',
  'run.cancelled',
  'run.failed',
  'run.quarantined',
]);

/** The kernel's approval of one call names it: `ix.approve.<invocationId>` (fold). */
export const APPROVAL_PREFIX = 'ix.approve.';
export { CONNECT_PREFIX };
export const CHILD_ROLE = 'pipeshub-subagent';
const CHILD_STATUS: Readonly<Record<string, ChildStatus>> = {
  answered: 'completed',
  needs_input: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
};
/** A `child.completed` outcome as the sub-agent frames and parts show it. */
export const childStatus = (outcome: unknown): ChildStatus =>
  CHILD_STATUS[String(outcome)] ?? 'completed';
/**
 * What a saved conversation keeps of an engine's output and diffs; the card's
 * structure (commands, files, versions, artifacts, outcome) is always kept.
 */
export const MAX_PERSISTED_ENGINE_CHARS = 200_000;
const DROPPABLE_ENGINE_KINDS: ReadonlySet<string> = new Set(['output', 'diff']);

/** The worker's preview of a held call, as far as it is well formed; a malformed field is left out. */
export function approvalEffect(value: unknown): ApprovalEffect | undefined {
  if (value === null || typeof value !== 'object' || Array.isArray(value))
    return undefined;
  const data = value as Record<string, unknown>;
  const effect: ApprovalEffect = {};
  if (typeof data['diff'] === 'string' && data['diff'].length > 0) {
    effect.diff = data['diff'];
    if (data['diffTruncated'] === true) effect.diffTruncated = true;
  }
  const cost = data['costEstimate'];
  if (cost !== null && typeof cost === 'object' && !Array.isArray(cost)) {
    const usd = (cost as Record<string, unknown>)['usd'];
    if (typeof usd === 'number' && Number.isFinite(usd) && usd >= 0)
      effect.costEstimate = { usd, basis: 'declared' };
  }
  return Object.keys(effect).length > 0 ? effect : undefined;
}

const str = (value: unknown): string | undefined =>
  typeof value === 'string' ? value : undefined;
/** `run.failed.servers` (WP-84): the labels of the servers that withdrew a tool; nothing when there are none. */
const failedServers = (value: unknown): { servers?: string[] } => {
  const servers = Array.isArray(value)
    ? value.filter(
        (label): label is string =>
          typeof label === 'string' && label.length > 0,
      )
    : [];
  return servers.length > 0 ? { servers } : {};
};
/** `run.failed.memoryLimitMb` (R31GVISO-FU-3): the limit an out-of-memory run met; nothing when malformed. */
const failedMemoryLimit = (value: unknown): { memoryLimitMb?: number } =>
  typeof value === 'number' && Number.isInteger(value) && value > 0
    ? { memoryLimitMb: value }
    : {};
const record = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};

const SITE_ACCESSES: ReadonlySet<string> = new Set([
  'read',
  'read-write',
  'host-level',
]);
const SITE_USES: ReadonlySet<string> = new Set(['read', 'write']);

/** An egress record's `site` (WP-63) as the activity reads it; anything malformed is no site. */
const siteOf = (
  value: unknown,
): { path: string; access: SiteAccessShown; use: SiteUse } | undefined => {
  const site = record(value);
  const path = str(site['path']);
  const access = str(site['access']);
  const use = str(site['use']);
  return path !== undefined &&
    path.startsWith('/') &&
    access !== undefined &&
    SITE_ACCESSES.has(access) &&
    use !== undefined &&
    SITE_USES.has(use)
    ? { path, access: access as SiteAccessShown, use: use as SiteUse }
    : undefined;
};

interface ContentRefShape {
  ref?: unknown;
}

/**
 * A worker content ref the projector can read without the worker's store:
 * only `inline:` refs. Everything the stream shows is journaled as
 * presentation, so this is a fallback for journals without it.
 */
export function inlineContent(ref: unknown): string | null {
  const raw = str((ref as ContentRefShape | undefined)?.ref);
  if (!raw?.startsWith('inline:')) return null;
  try {
    return Buffer.from(raw.slice('inline:'.length), 'base64url').toString(
      'utf8',
    );
  } catch {
    return null;
  }
}

function inlineJson(ref: unknown): Record<string, unknown> | null {
  const text = inlineContent(ref);
  if (text === null) return null;
  try {
    return record(JSON.parse(text));
  } catch {
    return null;
  }
}

const TASK_STATUSES: ReadonlySet<string> = new Set([
  'pending',
  'in_progress',
  'completed',
  'cancelled',
]);

const SENSITIVITIES: ReadonlySet<string> = new Set([
  'public',
  'private',
  'unknown',
]);
const httpUrl = (value: unknown): string | undefined => {
  const text = str(value);
  return text !== undefined && /^https?:\/\//.test(text) ? text : undefined;
};

// The reason's own fields; any other is an edition's (its overlay of interaction.explained, which the journal
// validated), carried to the card and the approvals list as journaled.
const OWN_REASON_FIELDS: ReadonlySet<string> = new Set([
  'interactionId',
  'rule',
  'layer',
  'toolId',
  'sensitivity',
  'sources',
  'moreSources',
  'destination',
  'argsDigest',
  'conversationGrant',
  'editable',
  'fieldOrder',
  'ruleGrant',
]);

/** The fields an edition's rule adds to a reason, as journaled; empty for a reason of this build's own rules. */
export function editionReasonFields(
  data: Readonly<Record<string, unknown>>,
): Readonly<Record<string, unknown>> {
  return Object.fromEntries(
    Object.entries(data).filter(([field]) => !OWN_REASON_FIELDS.has(field)),
  );
}

/**
 * A name, a destination or a title a decision surface shows, display-safe (SEC-B-04, ADR-TEXT-01): the worker
 * already wrote it so, and this holds for a journal an older worker wrote or any other writer.
 */
const safeText = (value: unknown): string | undefined => {
  const text = str(value);
  return text === undefined ? undefined : displaySafe(text);
};

/** The worker's reason as the card shows it; never the destination's grant key. */
export function approvalReason(
  data: Record<string, unknown>,
): ApprovalReason | null {
  const rule = str(data['rule']);
  const sensitivity = str(data['sensitivity']);
  if (
    rule === undefined ||
    (sensitivity !== undefined && !SENSITIVITIES.has(sensitivity))
  ) {
    return null;
  }
  const layer = ruleLayer(data['layer']);
  const sources = Array.isArray(data['sources'])
    ? data['sources'].map(record).map((source) => {
        const title = safeText(source['title']);
        const recordId = str(source['recordId']);
        const url = httpUrl(source['url']);
        return {
          ...(title !== undefined ? { title } : {}),
          ...(recordId !== undefined ? { recordId } : {}),
          ...(url !== undefined ? { url } : {}),
        };
      })
    : [];
  const more = data['moreSources'];
  const label = safeText(record(data['destination'])['label']);
  const fieldOrder = Array.isArray(data['fieldOrder'])
    ? data['fieldOrder']
        .filter(
          (name): name is string =>
            typeof name === 'string' && name.length > 0 && name.length <= 128,
        )
        .slice(0, 64)
    : [];
  return {
    rule,
    ...(layer !== undefined ? { layer } : {}),
    ...(sensitivity !== undefined
      ? {
          sensitivity: sensitivity as NonNullable<
            ApprovalReason['sensitivity']
          >,
        }
      : {}),
    sources,
    moreSources: typeof more === 'number' && more > 0 ? more : 0,
    ...(label !== undefined ? { destination: label } : {}),
    conversationGrant: data['conversationGrant'] === true,
    ...(typeof data['ruleGrant'] === 'object' && data['ruleGrant'] !== null
      ? { ruleGrant: true as const }
      : {}),
    ...(data['editable'] === true ? { editable: true as const } : {}),
    ...(data['editable'] === true && fieldOrder.length > 0
      ? { fieldOrder }
      : {}),
    ...editionReasonFields(data),
  };
}

const parseInvocation = (
  invocationId: unknown,
): { turn: number; index: number } | null => {
  const match = /^t(\d+)\.(\d+)$/.exec(str(invocationId) ?? '');
  return match ? { turn: Number(match[1]), index: Number(match[2]) } : null;
};

/** Legacy statuses of a tool part from a `tool.outcome` status. */
const partStatus = (outcome: string): 'completed' | 'failed' =>
  outcome === 'ok' ? 'completed' : 'failed';

export class TimelineBuilder {
  readonly timeline: Timeline;
  private turn = 0;
  private attempt = 0;
  private readonly byCall = new Map<string, CallView>();
  private readonly denied = new Set<string>();
  /** An engine's approval line names the call its interaction is about (P2.6c FB-7). */
  private readonly engineCalls = new Map<
    string,
    {
      toolName: string;
      argsJson: string;
      displayName?: string;
      effect?: ApprovalEffect;
    }
  >();
  private engineChars = 0;
  /** Lines journaled before FIX-SKILLSHOW and FIX-TOOLWORDS, read as they write them now. */
  private readonly legacyLines = new LegacyEngineLines();
  /** The engine a `code_task` hand-off runs on (CTL-6 `x.pipeshub.codeTask`), so the live step can name it. */
  codeTaskEngine: string | undefined;
  /** DEEPROUTE: the hand-off a turn boundary journaled, for the `code_task` child that follows it. */
  private handoff: CodeTaskHandoff | undefined;
  private counted: Map<string, Set<string>> | undefined;
  /** WP-113: the run's approvals decided so far, which names the asks that waited together (`decidedBefore`). */
  private decidedApprovals = 0;
  /** `interaction.explained` precedes its request in the same commit (UX1). */
  private readonly reasons = new Map<string, ApprovalReason>();
  /** The tool each journaled reason names: a card the call's line never reached still says what it is (WP-97). */
  private readonly explainedTools = new Map<string, string>();
  private called = false;
  /** When the step before the next model call ended: the claim, a response or a tool outcome (CTL-3b). */
  private stepEnd: string | undefined;

  constructor(runId: string) {
    this.timeline = {
      runId,
      turns: [],
      questions: [],
      unavailable: [],
      artifacts: [],
      inputs: [],
      answer: null,
      end: null,
      outcomes: [],
      engine: null,
      approvals: [],
      connects: [],
      children: [],
      tasks: [],
      currentTaskId: null,
      notices: [],
    };
  }

  approval(interactionId: unknown): ApprovalView | undefined {
    return this.timeline.approvals.find(
      (view) => view.interactionId === interactionId,
    );
  }

  child(childRunId: unknown): ChildView | undefined {
    return this.timeline.children.find((view) => view.runId === childRunId);
  }

  call(callId: string): CallView | undefined {
    return this.byCall.get(callId);
  }

  callForInvocation(invocationId: unknown): CallView | undefined {
    const at = parseInvocation(invocationId);
    if (at === null) return undefined;
    return this.timeline.turns.find((t) => t.turn === at.turn)?.calls[at.index];
  }

  get currentTurn(): TurnView | undefined {
    return this.timeline.turns[this.timeline.turns.length - 1];
  }

  artifact(artifactId: unknown): ArtifactView | undefined {
    return typeof artifactId === 'string' && artifactId !== ''
      ? this.timeline.artifacts.find((view) => view.artifactId === artifactId)
      : undefined;
  }

  /** One view per artifact: a later presentation replaces it where it stands (U1). */
  private presented(view: ArtifactView): void {
    const artifacts = this.timeline.artifacts;
    const at =
      view.artifactId === ''
        ? -1
        : artifacts.findIndex((known) => known.artifactId === view.artifactId);
    const previous = artifacts[at];
    if (previous === undefined) {
      artifacts.push(view);
      return;
    }
    artifacts[at] =
      view.callId === undefined && previous.callId !== undefined
        ? { ...view, callId: previous.callId }
        : view;
  }

  input(messageSeq: unknown): InputView | undefined {
    return this.timeline.inputs.find((view) => view.messageSeq === messageSeq);
  }

  apply(event: JournalEvent): void {
    if (event.runId !== this.timeline.runId) return;
    const data = record(event.data);
    this.timeline.startedAt ??= event.ts;
    this.stepEnd ??= event.ts;
    // A claim starts an attempt's first model call; the run's end closes its total.
    if (event.type === 'run.claimed') this.stepEnd = event.ts;
    if (RUN_ENDS.has(event.type)) this.timeline.endedAt = event.ts;
    switch (event.type) {
      case 'turn.started':
        this.turn = Number(data['turn']) || this.turn;
        return;
      case 'model.request':
        this.attempt = Number(data['modelAttempt']) || this.attempt;
        this.modelCalled(data['route']);
        return;
      case 'run.spec_pinned': {
        const runKind = str(record(data['spec'])['runKind']);
        if (runKind !== undefined) this.timeline.runKind = runKind;
        // A person's /run asks no model (SLASH3): its pin names none it will call.
        if (runKind !== COMMAND_RUN) this.modelPinned(data['spec']);
        const executor = record(record(data['spec'])['executor']);
        const engineKind =
          executor['kind'] === 'engine'
            ? str(record(executor['engine'])['kind'])
            : undefined;
        if (engineKind !== undefined) this.timeline.engineKind = engineKind;
        this.timeline.executor = engineKind ?? 'loop';
        this.timeline.reasoningEffort =
          str(record(record(data['spec'])['model'])['reasoningEffort']) ?? null;
        this.timeline.display = runDisplay(
          record(record(record(data['spec'])['extensions'])['x.pipeshub'])[
            'resolved'
          ],
        );
        const granted = record(record(data['spec'])['grants'])['tools'];
        this.timeline.toolsGranted = Array.isArray(granted)
          ? granted.length
          : 0;
        const gates = runGatesSchema.safeParse(record(data['spec'])['gates']);
        if (gates.success) {
          this.timeline.gates = {
            dryRun: gates.data.dryRun === true,
            planApproval: gates.data.planApproval === 'required',
            verify: gates.data.verify !== undefined,
          };
          // G-03: the person reads that nothing in the answer was done before they read the answer.
          if (gates.data.dryRun === true) this.notice('dry_run');
        }
        const pipeshub = record(
          record(record(data['spec'])['extensions'])['x.pipeshub'],
        );
        this.codeTaskEngine = str(
          record(record(record(pipeshub['codeTask'])['profile'])['engine'])[
            'kind'
          ],
        );
        this.timeline.codingEngine = this.codeTaskEngine ?? null;
        return;
      }
      case 'model.response':
        this.response(event, data);
        return;
      // G-48: the run's own model's provider was down and the model policy moved it to an alternate the run pinned.
      case 'model.fallback': {
        const model = str(record(data['to'])['model']);
        if (model !== undefined)
          this.notice(MODEL_SWITCHED, codePoints(model).slice(0, 200).join(''));
        return;
      }
      // G-48: an engine run waited in the queue for a free sandbox (QA31F); the answer says it queued, once.
      case 'run.yielded':
        if (data['reason'] === 'sandbox_wait') this.notice(SANDBOX_QUEUED);
        return;
      case 'x.pipeshub.run.notice': {
        const code = str(data['code']);
        if (code === TIME_PLAN) this.timePlan(data);
        else if (code !== undefined)
          this.notice(
            code,
            str(data['item']),
            str(data['kind']),
            undefined,
            str(data['engine']),
            {
              reason: str(data['reason']),
              instance: str(data['instance']),
              tools: Array.isArray(data['tools'])
                ? data['tools'].filter(
                    (tool): tool is string => typeof tool === 'string',
                  )
                : undefined,
            },
          );
        return;
      }
      case 'x.pipeshub.run.handoff': {
        const trigger = str(data['trigger']);
        const reason = str(data['reason']);
        if (trigger !== undefined && reason !== undefined)
          this.handoff = { trigger, reason };
        return;
      }
      case 'x.pipeshub.command.expanded': {
        const name = str(data['name']);
        const version = str(data['version']);
        const owner = str(data['owner']);
        const expandedChars = data['expandedChars'];
        if (
          name !== undefined &&
          version !== undefined &&
          owner !== undefined &&
          typeof expandedChars === 'number'
        )
          this.timeline.command = { name, version, expandedChars, owner };
        return;
      }
      case 'x.pipeshub.context.compacted': {
        const executor = str(data['executor']);
        const outcome = str(data['outcome']);
        const focus = str(data['focus']);
        if (
          executor !== undefined &&
          outcome !== undefined &&
          COMPACT_OUTCOMES.has(outcome)
        )
          this.timeline.compacted = {
            executor,
            outcome: outcome as CompactedView['outcome'],
            beforeTokens: tokenCount(data['beforeTokens']),
            afterTokens: tokenCount(data['afterTokens']),
            userMessagesKept: data['userMessagesKept'] === true,
            ...(focus === undefined ? {} : { focus }),
          };
        return;
      }
      case 'x.pipeshub.goal.presented': {
        const goal = str(data['description']);
        if (goal !== undefined) this.timeline.goal = goal;
        return;
      }
      case 'x.pipeshub.message.presented': {
        const current = this.currentTurn;
        if (current !== undefined) {
          current.text = str(data['text']) ?? '';
          if (data['truncated'] === true) current.truncated = true;
          const answerFrom = data['answerFrom'];
          if (typeof answerFrom === 'number' && Number.isInteger(answerFrom))
            current.answerFrom = answerFrom;
          const reasoning = str(data['reasoning']);
          if (
            reasoning !== undefined &&
            reasoning.length > 0 &&
            this.timeline.display?.reasoning !== false
          )
            current.reasoning = reasoning;
        }
        return;
      }
      case 'x.pipeshub.tool.call_presented':
        this.presentCall(data);
        return;
      case 'tool.intent': {
        const view = this.callForInvocation(data['invocationId']);
        const effect = str(data['effect']);
        if (view !== undefined && (effect === 'write' || effect === 'execute'))
          view.writes = true;
        return;
      }
      case 'tool.denied':
        this.deny(data);
        return;
      case 'tool.outcome':
        this.outcome(data, event.ts);
        return;
      case 'verification.recorded': {
        const verified = verificationOf([event]);
        if (verified === null || data['continuing'] === true) return;
        this.timeline.verification = verified;
        const failed = verified.checks.find((check) => !check.passed);
        this.notice(
          verified.status,
          failed?.reason !== undefined
            ? codePoints(failed.reason).slice(0, 200).join('')
            : undefined,
          undefined,
          { checks: verified.checks, round: verified.round },
        );
        return;
      }
      case 'x.pipeshub.tool.result_presented':
        this.presentResult(data);
        return;
      case 'x.pipeshub.question.asked': {
        const callId = str(data['callId']) ?? '';
        // An engine's re-driven turn asks again under the question's own id (C3.70): one card, the latest.
        const shown = this.timeline.questions.findIndex(
          (q) => callId !== '' && q.callId === callId,
        );
        const question = {
          callId,
          status: str(data['status']) ?? 'success',
          toolData: data['toolData'],
        };
        if (shown >= 0) this.timeline.questions[shown] = question;
        else this.timeline.questions.push(question);
        return;
      }
      case 'x.pipeshub.tool.unavailable':
        this.timeline.unavailable.push({
          callId: str(data['callId']) ?? '',
          hits: (Array.isArray(data['hits'])
            ? (data['hits'] as unknown[])
            : []
          ).map((hit) =>
            Object.fromEntries(
              ['tool', 'toolset', 'reason', 'message'].map((key) => [
                key,
                str(record(hit)[key]) ?? '',
              ]),
            ),
          ),
        });
        return;
      case 'x.pipeshub.artifact.presented':
        this.presented(artifactView(data));
        return;
      case 'x.pipeshub.outcome.settled': {
        const view = outcomeView(data);
        if (view !== null) this.timeline.outcome = view;
        return;
      }
      case 'x.pipeshub.answer.finalized':
        this.timeline.answer = withoutHiddenReasoning(
          answerRecord(data),
          this.timeline.display,
        );
        return;
      case 'input.queued': {
        const seq = data['messageSeq'];
        if (typeof seq !== 'number' || this.input(seq) !== undefined) return;
        const attachments = data['attachments'];
        this.timeline.inputs.push({
          messageSeq: seq,
          text: inlineContent(data['textRef']) ?? '',
          afterTurns: null,
          ...(Array.isArray(attachments) && attachments.length > 0
            ? { attachments: attachments.map(record) }
            : {}),
        });
        return;
      }
      case 'input.consumed': {
        const view = this.input(data['messageSeq']);
        if (view?.afterTurns === null) {
          view.afterTurns = this.timeline.turns.length;
        }
        return;
      }
      case 'x.pipeshub.engine.progress':
        this.engineLine(data);
        return;
      case 'egress.allowed':
      case 'gateway.refused':
        this.siteLine(event.type, data);
        return;
      case SECRET_HIDDEN: {
        const hidden = withSecretHidden(this.timeline.secretsHidden, data);
        if (hidden !== undefined) this.timeline.secretsHidden = hidden;
        return;
      }
      case REPOSITORY_UNAVAILABLE: {
        const card = unavailableRepository(data);
        if (card !== null)
          (this.timeline.repositoriesUnavailable ??= []).push(card);
        return;
      }
      case REPOSITORY_FETCHED: {
        // WP-63 part A: a repository the worker fetched and handed to the run's sandbox.
        const line = fetchedRepositoryLine(data);
        if (line !== null) (this.timeline.sites ??= []).push(line);
        return;
      }
      case 'interaction.explained':
        if (event.writer === 'worker') this.explained(data);
        return;
      case 'interaction.requested':
        this.approvalRequested(event, data);
        this.connectRequested(event, data);
        return;
      case 'interaction.granted': {
        const view = this.approval(data['interactionId']);
        // FLOWGRANT: a copy of another run's grant answers none of this run's cards.
        if (
          view !== undefined &&
          event.writer === 'node' &&
          data['scope'] === 'conversation' &&
          !isSharedGrant(data)
        ) {
          view.scope = 'conversation';
        }
        return;
      }
      case 'interaction.resolved':
        this.approvalResolved(data);
        this.connectResolved(data);
        return;
      case APPROVAL_COUNTED:
        if (event.writer === 'node') this.approvalCounted(data);
        return;
      case 'task.created':
      case 'task.updated':
        this.task(data);
        return;
      case 'child.spawned':
        this.childSpawned(event, data);
        return;
      case 'child.completed':
        this.childCompleted(event, data);
        return;
      case 'run.completed':
        this.expireApprovals();
        this.timeline.end = {
          status: 'completed',
          outcome:
            data['outcome'] === 'needs_input' ? 'needs_input' : 'answered',
          ...cutShort(data),
        };
        return;
      case 'run.cancel_requested': {
        const stop = operatorStopOf(data['stop']);
        if (stop !== null) this.timeline.stoppedBy = stop;
        return;
      }
      case 'run.cancelled':
        this.expireApprovals();
        this.timeline.end = { status: 'cancelled' };
        return;
      case 'run.failed':
        this.expireApprovals();
        this.timeline.end = {
          status: 'failed',
          errorType: str(data['errorType']) ?? 'worker_failed',
          ...(str(data['message']) !== undefined
            ? { message: str(data['message']) }
            : {}),
          ...failedServers(data['servers']),
          ...failedMemoryLimit(data['memoryLimitMb']),
        };
        return;
      case 'run.quarantined':
        this.expireApprovals();
        this.timeline.end = {
          status: 'quarantined',
          errorType: str(data['lastErrorType']) ?? 'quarantined',
        };
        return;
      default:
        return;
    }
  }

  private task(data: Record<string, unknown>): void {
    const taskId = str(data['taskId']);
    const status = str(data['status']);
    if (
      taskId === undefined ||
      status === undefined ||
      !TASK_STATUSES.has(status)
    )
      return;
    const view: TaskView = {
      taskId,
      title: str(data['title']) ?? '',
      status: status as TaskStatus,
      deps: Array.isArray(data['deps'])
        ? data['deps'].filter((d): d is string => typeof d === 'string')
        : [],
      ...(typeof data['asksFirst'] === 'boolean'
        ? { asksFirst: data['asksFirst'] }
        : {}),
    };
    const tasks = this.timeline.tasks;
    const at = tasks.findIndex((known) => known.taskId === taskId);
    if (at >= 0) tasks[at] = view;
    else tasks.push(view);
    if (view.status === 'in_progress') this.timeline.currentTaskId = taskId;
    else if (this.timeline.currentTaskId === taskId)
      this.timeline.currentTaskId = null;
  }

  /** The step in progress, as a field to tag what the run does under it. */
  private get step(): { stepId?: string } {
    const current = this.timeline.currentTaskId;
    return current !== null ? { stepId: current } : {};
  }

  /** WP-63: an allowed website's request, or any request the egress proxy refused, as an activity line. */
  private siteLine(type: string, data: Record<string, unknown>): void {
    const host = str(data['host']);
    const site = siteOf(data['site']);
    const line =
      type === 'egress.allowed'
        ? host !== undefined && site !== undefined
          ? allowedSiteLine(host, site)
          : null
        : data['gateway'] === 'egress'
          ? blockedSiteLine(str(data['reason']) ?? '', host, site)
          : null;
    if (line !== null) (this.timeline.sites ??= []).push(line);
  }

  private engineLine(data: Record<string, unknown>): void {
    const kind = str(data['kind']) ?? '';
    const text = str(data['text']) ?? '';
    let line: EngineFrameView = this.legacyLines.line({
      tool: str(data['tool']) ?? '',
      taskId: str(data['taskId']) ?? '',
      kind,
      text,
      data: record(data['data']),
      ...this.step,
    });
    if (line.kind === 'approval') {
      const interactionId = str(line.data['interactionId']);
      if (interactionId !== undefined) {
        const displayName = safeText(line.data['displayName']);
        const effect = approvalEffect(line.data['effect']);
        this.engineCalls.set(interactionId, {
          toolName: str(line.data['toolName']) ?? (line.text || 'tool'),
          argsJson: str(line.data['argsJson']) ?? '{}',
          ...(displayName !== undefined ? { displayName } : {}),
          ...(effect !== undefined ? { effect } : {}),
        });
      }
    }
    const engine = (this.timeline.engine ??= {
      taskId: line.taskId,
      frames: [],
      truncated: false,
    });
    let size = line.text.length + JSON.stringify(line.data).length;
    const over = this.engineChars + size > MAX_PERSISTED_ENGINE_CHARS;
    if (DROPPABLE_ENGINE_KINDS.has(line.kind) && over) {
      engine.truncated = true;
      return;
    }
    // A tool's result goes over the bound like output; its step still ends, so the line stays without it.
    if (line.kind === 'tool_result' && over && 'result' in line.data) {
      const { result: _result, ...rest } = line.data;
      line = { ...line, data: { ...rest, truncated: true } };
      size = line.text.length + JSON.stringify(line.data).length;
      engine.truncated = true;
    }
    this.engineChars += size;
    engine.frames.push(line);
  }

  private approvalRequested(
    event: JournalEvent,
    data: Record<string, unknown>,
  ): void {
    const interactionId = str(data['interactionId']);
    if (
      data['kind'] !== 'approval' ||
      interactionId === undefined ||
      !interactionId.startsWith(APPROVAL_PREFIX) ||
      this.approval(interactionId) !== undefined
    ) {
      return;
    }
    const call = this.callForInvocation(
      interactionId.slice(APPROVAL_PREFIX.length),
    );
    const engine = this.engineCalls.get(interactionId);
    const explainedTool = this.explainedTools.get(interactionId);
    // WP-97: a request whose call line is missing is still a card, from the tool its reason names; never nothing.
    if (
      call === undefined &&
      engine === undefined &&
      explainedTool === undefined
    )
      return;
    const reason = this.reasons.get(interactionId);
    this.timeline.approvals.push({
      interactionId,
      toolCallId: call?.callId ?? interactionId,
      toolName: call?.name ?? engine?.toolName ?? explainedTool ?? 'tool',
      ...((call?.displayName ?? engine?.displayName) !== undefined
        ? { displayName: call?.displayName ?? engine?.displayName }
        : {}),
      args: call?.argsJson ?? engine?.argsJson ?? '{}',
      ...(call?.argsSummary !== undefined
        ? { argsSummary: call.argsSummary }
        : {}),
      status: 'pending',
      ...(reason !== undefined ? { reason } : {}),
      ...(engine?.effect !== undefined ? { effect: engine.effect } : {}),
      afterTurns: this.timeline.turns.length,
      decidedBefore: this.decidedApprovals,
      seq: event.seq,
      requestedAt: event.ts,
      ...this.step,
    });
  }

  private explained(data: Record<string, unknown>): void {
    const interactionId = str(data['interactionId']);
    const reason = approvalReason(data);
    if (interactionId !== undefined && reason !== null) {
      this.reasons.set(interactionId, reason);
      const toolId = str(data['toolId']);
      if (toolId !== undefined) this.explainedTools.set(interactionId, toolId);
    }
  }

  /** One more distinct person approved an ask that needs several (Node's count, by principal; the event names how many). */
  private approvalCounted(data: Record<string, unknown>): void {
    const view = this.approval(data['interactionId']);
    const by = data['by'];
    const required = data['required'];
    if (
      view === undefined ||
      typeof by !== 'string' ||
      typeof required !== 'number' ||
      !Number.isInteger(required) ||
      required < 1
    ) {
      return;
    }
    const people = (this.counted ??= new Map<string, Set<string>>());
    const seen = people.get(view.interactionId) ?? new Set<string>();
    seen.add(by);
    people.set(view.interactionId, seen);
    view.approvals = { counted: seen.size, required };
  }

  private approvalResolved(data: Record<string, unknown>): void {
    const view = this.approval(data['interactionId']);
    if (view === undefined) return;
    this.decidedApprovals += 1;
    const decision = data['decision'];
    view.status =
      decision === 'approved' || decision === 'denied' ? decision : 'closed';
    if (decision === 'denied') {
      // WP-63 part A: a clone or push the person refused is a line of the run's activity too.
      const tool = [REPOSITORY_FETCH_TOOL, REPOSITORY_PUSH_TOOL].find((name) =>
        isRepositoryTool(view.toolName, name),
      );
      if (tool !== undefined)
        (this.timeline.sites ??= []).push(
          deniedRepositoryLine(tool, view.reason?.destination),
        );
    }
    // WP-63: the upload card's answer, for the site it names.
    if (
      isRepositoryTool(view.toolName, SITE_UPLOAD_TOOL) &&
      view.reason?.destination !== undefined
    ) {
      if (decision === 'approved' || decision === 'denied') {
        (this.timeline.sites ??= []).push(
          siteUploadDecisionLine(
            view.reason.destination,
            decision === 'approved',
          ),
        );
      }
    }
    // Only a denial journals a reason, inline (AP-05); anything else carries none.
    const reason =
      decision === 'denied'
        ? inlineContent(data['answerRef'])?.trim()
        : undefined;
    if (reason) view.denyReason = reason.slice(0, MAX_DENY_REASON_CHARS);
    // An approval's answer is the arguments the person approved instead (AP-06).
    const edited =
      decision === 'approved' ? inlineContent(data['answerRef']) : undefined;
    if (typeof edited === 'string' && edited.length > 0)
      view.editedArgs = edited;
  }

  private modelCalled(route: unknown): void {
    const label = modelLabel(route);
    if (label === undefined) return;
    this.called = true;
    this.timeline.model = label;
  }

  /** What the run will call, until its journal says what it called. */
  private modelPinned(spec: unknown): void {
    if (this.called) return;
    const policy = record(record(spec)['model'])['policy'];
    const label = modelLabel(Array.isArray(policy) ? policy[0] : undefined);
    if (
      label === undefined ||
      (label.provider === ORG_DEFAULT_MODEL.provider &&
        label.model === ORG_DEFAULT_MODEL.model)
    )
      return;
    this.timeline.model = label;
  }

  /** The run ended while a person still had to decide: nobody can now. */
  private expireApprovals(): void {
    for (const view of this.timeline.approvals) {
      if (view.status === 'pending') view.status = 'expired';
    }
    for (const view of this.timeline.connects) {
      if (view.status === 'pending') view.status = 'closed';
    }
  }

  connect(interactionId: unknown): ConnectView | undefined {
    return typeof interactionId === 'string'
      ? this.timeline.connects.find((c) => c.interactionId === interactionId)
      : undefined;
  }

  private connectRequested(
    event: JournalEvent,
    data: Record<string, unknown>,
  ): void {
    const interactionId = str(data['interactionId']);
    const connect = record(data['connect']);
    const item = str(connect['item']);
    const itemKind = connect['itemKind'];
    const instanceId = str(connect['instanceId']);
    if (
      data['kind'] !== 'connect' ||
      interactionId === undefined ||
      !interactionId.startsWith(CONNECT_PREFIX) ||
      item === undefined ||
      instanceId === undefined ||
      (itemKind !== 'toolset' && itemKind !== 'mcp') ||
      this.connect(interactionId) !== undefined
    ) {
      return;
    }
    const call = this.callForInvocation(
      interactionId.slice(CONNECT_PREFIX.length),
    );
    this.timeline.connects.push({
      interactionId,
      toolCallId: call?.callId ?? interactionId,
      ...(call !== undefined ? { toolName: call.name } : {}),
      ...(call?.displayName !== undefined
        ? { displayName: call.displayName }
        : {}),
      item,
      itemKind,
      instanceId,
      connectPath: CONNECT_PATHS[itemKind],
      ...(connect['reason'] === 'sign_in_rejected'
        ? { reason: 'sign_in_rejected' as const }
        : {}),
      askedAt: event.ts,
      status: 'pending',
      afterTurns: this.timeline.turns.length,
      seq: event.seq,
      ...this.step,
    });
  }

  private connectResolved(data: Record<string, unknown>): void {
    const view = this.connect(data['interactionId']);
    if (view === undefined) return;
    const decision = data['decision'];
    view.status =
      decision === 'approved'
        ? 'connected'
        : decision === 'denied'
          ? 'declined'
          : decision === 'expired'
            ? 'expired'
            : 'closed';
  }

  private childSpawned(
    event: JournalEvent,
    data: Record<string, unknown>,
  ): void {
    const runId = str(data['childRunId']);
    if (runId === undefined || this.child(runId) !== undefined) return;
    const invocationId = str(data['invocationId']);
    const taskId = str(data['taskId']) ?? null;
    const model = modelLabel(data['model']);
    const role = str(record(data['model'])['role']);
    const codeTaskGoal = this.callGoal(invocationId, CODE_TASK_TOOL);
    const goal = jobTitle(this.callGoal(invocationId, SPAWN_AGENT_TOOL));
    const handed = codeTaskGoal !== undefined ? this.handoff : undefined;
    if (handed !== undefined) this.handoff = undefined;
    // The engine the run pinned for its hand-offs, so the row says "Handed to Codex: …".
    const handoff =
      handed !== undefined && this.codeTaskEngine !== undefined
        ? { ...handed, engine: this.codeTaskEngine }
        : handed;
    this.timeline.children.push({
      runId,
      ...(invocationId !== undefined ? { invocationId } : {}),
      taskId,
      role: this.childRole(invocationId, taskId),
      status: 'running',
      startedAt: event.ts,
      updatedAt: event.ts,
      afterTurns: this.timeline.turns.length,
      seq: event.seq,
      ...(model !== undefined && role !== undefined
        ? { model: { ...model, role } }
        : {}),
      ...(codeTaskGoal !== undefined ? { codeTaskGoal } : {}),
      ...(goal !== undefined ? { goal } : {}),
      ...(handoff !== undefined ? { codeTaskHandoff: handoff } : {}),
    });
  }

  /** The `goal` argument of the hand-off call that spawned a child, when that call is `tool`. */
  private callGoal(invocationId: unknown, tool: string): string | undefined {
    const call = this.callForInvocation(invocationId);
    if (call?.name !== tool) return undefined;
    try {
      const goal = (JSON.parse(call.argsJson) as Record<string, unknown>)[
        'goal'
      ];
      return typeof goal === 'string' && goal.trim() !== ''
        ? goal.trim()
        : undefined;
    } catch {
      return undefined;
    }
  }

  private childCompleted(
    event: JournalEvent,
    data: Record<string, unknown>,
  ): void {
    const view = this.child(data['childRunId']);
    if (view === undefined) return;
    view.status = childStatus(data['outcome']);
    view.updatedAt = event.ts;
    const errorType = str(data['errorType']);
    if (errorType !== undefined) view.errorType = errorType;
    const { memoryLimitMb } = failedMemoryLimit(data['memoryLimitMb']);
    if (memoryLimitMb !== undefined) view.memoryLimitMb = memoryLimitMb;
  }

  /** Legacy's `role_name` for a sub-agent: its spawn call's `role` (from the call's presentation). */
  private childRole(invocationId: unknown, taskId: string | null): string {
    const call = this.callForInvocation(invocationId);
    let role: unknown;
    try {
      role =
        call !== undefined
          ? (JSON.parse(call.argsJson) as Record<string, unknown>)['role']
          : undefined;
    } catch {
      role = undefined;
    }
    const name = typeof role === 'string' && role !== '' ? role : taskId;
    return name === null ? CHILD_ROLE : `${CHILD_ROLE}-${name}`;
  }

  private response(event: JournalEvent, data: Record<string, unknown>): void {
    const message = inlineJson(data['messageRef']);
    const argsById = new Map<string, unknown>();
    const toolCalls = message?.['toolCalls'];
    for (const call of Array.isArray(toolCalls)
      ? (toolCalls as unknown[])
      : []) {
      const shaped = record(call);
      argsById.set(str(shaped['id']) ?? '', shaped['args']);
    }
    const calls = (
      Array.isArray(data['toolCalls']) ? (data['toolCalls'] as unknown[]) : []
    ).map((raw, index): CallView => {
      const call = record(raw);
      const callId = str(call['callId']) ?? '';
      const view: CallView = {
        callId,
        name: str(call['modelName']) ?? 'tool',
        turn: this.turn,
        index,
        argsJson: JSON.stringify(argsById.get(callId) ?? {}),
        hidden: false,
        ...this.step,
        startedAt: event.ts,
      };
      this.byCall.set(callId, view);
      return view;
    });
    this.timeline.turns.push({
      turn: this.turn,
      fence: event.fence ?? 0,
      modelAttempt: this.attempt,
      text: str(message?.['text']) ?? '',
      ...(data['provider'] === SYNTHETIC_PROVIDER &&
      data['transport'] === SYNTHETIC_TRANSPORT
        ? { synthetic: true }
        : {}),
      calls,
      ...this.step,
      ...(this.stepEnd !== undefined ? { startedAt: this.stepEnd } : {}),
      respondedAt: event.ts,
    });
    this.stepEnd = event.ts;
  }

  private presentCall(data: Record<string, unknown>): void {
    const view = this.byCall.get(str(data['callId']) ?? '');
    if (view === undefined) return;
    view.name = str(data['toolName']) ?? view.name;
    view.argsJson = str(data['argsJson']) ?? view.argsJson;
    const args = str(data['args']);
    if (args !== undefined) view.args = args;
    this.keepSaved(view, 'args', data['savedArgs']);
    const summary = str(data['argsSummary']);
    if (summary !== undefined) view.argsSummary = summary;
    const display = safeText(data['displayName']);
    if (display !== undefined) view.displayName = display;
    view.hidden = data['hidden'] === true;
    if (data['streamsAnswer'] === true) view.streamsAnswer = true;
    const step = toolStep(data['step']);
    if (step !== undefined) view.step = step;
  }

  private keepSaved(
    view: CallView,
    field: keyof NonNullable<CallView['saved']>,
    value: unknown,
  ): void {
    const saved = str(value);
    if (saved !== undefined) view.saved = { ...view.saved, [field]: saved };
  }

  private deny(data: Record<string, unknown>): void {
    const view = this.callForInvocation(data['invocationId']);
    if (view === undefined) return;
    this.denied.add(view.callId);
    view.status = 'blocked';
    const rule = str(data['rule']);
    if (rule !== undefined) view.blockedBy = rule;
  }

  /** A notice beside the run's steps, each once (by code and item). */
  /** ADR-RUN-01 D4: the run's time plan; a later turn's plan replaces the one before it. */
  private timePlan(data: Record<string, unknown>): void {
    const writeBy = str(data['writeBy']);
    const finishBy = str(data['finishBy']);
    if (writeBy === undefined || finishBy === undefined) return;
    this.timeline.notices = this.timeline.notices.filter(
      (notice) => notice.code !== TIME_PLAN,
    );
    this.timeline.notices.push({ code: TIME_PLAN, writeBy, finishBy });
  }

  private notice(
    code: string,
    item?: string,
    kind?: string,
    verification?: Pick<NoticeView, 'checks' | 'round'>,
    engine?: string,
    mcp: Pick<NoticeView, 'reason' | 'instance' | 'tools'> = {},
  ): void {
    const earlier = this.timeline.notices.filter(
      (notice) => notice.code === code && notice.item === item,
    ).length;
    const repeats = REPEATING_NOTICES.has(code);
    if (earlier > 0 && !repeats) return;
    this.timeline.notices.push({
      code,
      ...(repeats ? { occurrence: earlier + 1 } : {}),
      ...(item === undefined ? {} : { item }),
      ...(kind === undefined ? {} : { kind }),
      ...(engine === undefined ? {} : { engine }),
      ...(mcp.reason === undefined ? {} : { reason: mcp.reason }),
      ...(mcp.instance === undefined ? {} : { instance: mcp.instance }),
      ...(mcp.tools === undefined ? {} : { tools: [...mcp.tools] }),
      ...(verification === undefined
        ? {}
        : {
            checks: (verification.checks ?? []).map((check) => ({ ...check })),
            ...(verification.round === undefined
              ? {}
              : { round: verification.round }),
          }),
    });
  }

  private outcome(data: Record<string, unknown>, at: string): void {
    const view = this.callForInvocation(data['invocationId']);
    if (view === undefined) return;
    view.endedAt = at;
    if (this.stepEnd === undefined || at > this.stepEnd) this.stepEnd = at;
    const status = str(data['status']) ?? 'error';
    view.outcome = status;
    const shadow = record(data['shadow']);
    if (typeof shadow['wouldAsk'] === 'boolean') {
      view.shadow = { wouldAsk: shadow['wouldAsk'] };
    }
    if (this.denied.has(view.callId)) {
      view.content = resultText(data['resultRef']) ?? view.content;
      return;
    }
    view.status = partStatus(status);
    view.content = resultText(data['resultRef']) ?? '';
    const code = status === 'ok' ? undefined : resultCode(data['resultRef']);
    if (code !== undefined) {
      view.errorCode = code;
      view.detail = view.content;
      view.content = '';
    }
    if (status !== 'cancelled') {
      this.timeline.outcomes.push({ name: view.name, ok: status === 'ok' });
    }
  }

  private presentResult(data: Record<string, unknown>): void {
    const view = this.byCall.get(str(data['callId']) ?? '');
    if (view === undefined || this.denied.has(view.callId)) return;
    const status = str(data['status']);
    if (status === 'completed' || status === 'failed' || status === 'blocked') {
      view.status = status;
    }
    if (view.errorCode === undefined) {
      view.content = str(data['content']) ?? view.content;
      const summary = str(data['resultSummary']);
      if (summary !== undefined) view.resultSummary = summary;
      this.keepSaved(view, 'content', data['savedContent']);
      this.keepSaved(view, 'resultSummary', data['savedSummary']);
    }
    const artifactId = str(data['artifactId']);
    if (artifactId !== undefined) view.artifactId = artifactId;
    // The worker builds the result's facts on the call's: what came back replaces what was asked.
    const step = toolStep(data['step']);
    if (step !== undefined) view.step = step;
    const resultView = data['resultView'];
    if (
      typeof resultView === 'object' &&
      resultView !== null &&
      !Array.isArray(resultView)
    ) {
      view.resultView = resultView as Record<string, unknown>;
    }
  }
}

const TOOL_ERROR_CODE_SET: ReadonlySet<string> = new Set(TOOL_ERROR_CODES);

/** A presented call's step facts, as their contract reads them; anything else is no step. */
function toolStep(value: unknown): PipeshubToolStep | undefined {
  if (value === undefined) return undefined;
  const parsed = pipeshubToolStepSchema.safeParse(value);
  return parsed.success ? parsed.data : undefined;
}

/** A failed outcome's code, kept beside its detail in the result document (ERR-UX); an older worker wrote none. */
function resultCode(ref: unknown): ToolErrorCode | undefined {
  const code = inlineJson(ref)?.['errorCode'];
  return typeof code === 'string' && TOOL_ERROR_CODE_SET.has(code)
    ? (code as ToolErrorCode)
    : undefined;
}

/** The legacy 200-character preview of a result the worker did not present. */
function resultText(ref: unknown): string | undefined {
  const doc = inlineJson(ref);
  if (doc === null) return undefined;
  const content = doc['content'];
  const text = typeof content === 'string' ? content : JSON.stringify(content);
  return codePoints(text).slice(0, 200).join('');
}

function artifactView(data: Record<string, unknown>): ArtifactView {
  const version = data['version'];
  return {
    ...(str(data['callId']) !== undefined
      ? { callId: str(data['callId']) }
      : {}),
    artifactId: str(data['artifactId']) ?? '',
    fileName: str(data['fileName']) ?? '',
    mimeType: str(data['mimeType']) ?? 'application/octet-stream',
    sizeBytes: Number(data['sizeBytes']) || 0,
    artifactType: str(data['artifactType']) ?? '',
    isTemporary: data['isTemporary'] === true,
    ...(str(data['recordId']) !== undefined
      ? { recordId: str(data['recordId']) }
      : {}),
    ...(str(data['documentId']) !== undefined
      ? { documentId: str(data['documentId']) }
      : {}),
    ...(typeof version === 'number' ? { version } : {}),
    ...(str(data['derivedFromCodeArtifactId']) !== undefined
      ? { derivedFromCodeArtifactId: str(data['derivedFromCodeArtifactId']) }
      : {}),
    visibility: str(data['visibility']) ?? 'VISIBLE',
    card: data['card'] === true,
    ...(LIFECYCLES.has(str(data['lifecycle']) ?? '')
      ? { lifecycle: str(data['lifecycle']) as ArtifactLifecycle }
      : {}),
  };
}

/** C4.10: an answer of a run whose pin hides reasoning keeps none of it. */
function withoutHiddenReasoning(
  answer: AnswerRecord,
  display: RunDisplay | undefined,
): AnswerRecord {
  if (display?.reasoning !== false || answer.reasoning === undefined)
    return answer;
  const { reasoning: _hidden, ...kept } = answer;
  return kept;
}

function answerRecord(data: Record<string, unknown>): AnswerRecord {
  const outcome = str(data['outcome']);
  return {
    outcome:
      outcome === 'needs_input' || outcome === 'stopped' || outcome === 'error'
        ? outcome
        : 'answered',
    answer: str(data['answer']) ?? '',
    partTexts: Array.isArray(data['partTexts'])
      ? (data['partTexts'] as unknown[]).map((t) => str(t) ?? '')
      : [],
    citations: Array.isArray(data['citations'])
      ? (data['citations'] as unknown[])
      : [],
    confidence: str(data['confidence']) ?? null,
    ...(str(data['answerMatchType']) !== undefined
      ? { answerMatchType: str(data['answerMatchType']) }
      : {}),
    ...(str(data['errorCode']) !== undefined
      ? { errorCode: str(data['errorCode']) }
      : {}),
    ...(Array.isArray(data['reasoning'])
      ? { reasoning: (data['reasoning'] as unknown[]).map(record) }
      : {}),
    ...(Array.isArray(data['mcpSignIn'])
      ? { mcpSignIn: (data['mcpSignIn'] as unknown[]).map(record) }
      : {}),
    ...cutShort(data),
    ...budgetScopeField(data),
    ...notFoundOf(data),
    ...couldNotDoOf(data),
  };
}

/** The answer's typed "could not do" fact (R32REPO-FU-1); items this build cannot word are dropped, and with none
 * left, the whole fact (the answer still starts with its English line). */
export function couldNotDoOf(data: Record<string, unknown>): {
  limits?: CouldNotDoView;
} {
  const value = record(data['limits']);
  const line = str(value['line']);
  const raw = value['items'];
  if (line === undefined || line === '' || !Array.isArray(raw)) return {};
  const items = raw.flatMap((item) => {
    const parsed = pipeshubCouldNotDoSchema.safeParse(item);
    return parsed.success ? [parsed.data] : [];
  });
  return items.length > 0 ? { limits: { items, line } } : {};
}

/** The answer's typed not-found fact, when it is whole (FU-2); one this build cannot read is dropped. */
export function notFoundOf(data: Record<string, unknown>): {
  notFound?: NotFoundView;
} {
  const value = record(data['notFound']);
  const subject = str(value['subject']);
  const line = str(value['line']);
  const hits = value['hits'];
  if (
    subject === undefined ||
    line === undefined ||
    typeof hits !== 'number' ||
    !Number.isInteger(hits) ||
    hits < 0
  ) {
    return {};
  }
  const strings = (raw: unknown): string[] =>
    Array.isArray(raw)
      ? raw.filter(
          (name): name is string => typeof name === 'string' && name !== '',
        )
      : [];
  const near = strings(value['near']);
  const places = strings(value['places']);
  return {
    notFound: {
      subject,
      hits,
      line,
      ...(near.length > 0 ? { near } : {}),
      ...(value['proceeded'] === true ? { proceeded: true as const } : {}),
      ...(places.length > 0 ? { places } : {}),
    },
  };
}

/** Which shared budget cut the answer short, only beside a budget cut and only in a scope's shape. */
export function budgetScopeField(data: Record<string, unknown>): {
  budgetScope?: BudgetScope;
} {
  const value = data['budgetScope'];
  return data['truncatedBy'] === 'budget' && isBudgetScope(value)
    ? { budgetScope: value }
    : {};
}

/** `truncatedBy` when it names a limit this build knows; an unknown one is dropped. */
export function cutShort(data: Record<string, unknown>): {
  truncatedBy?: CutShortReason;
} {
  const value = data['truncatedBy'];
  return typeof value === 'string' &&
    (CUT_SHORT_REASONS as readonly string[]).includes(value)
    ? { truncatedBy: value as CutShortReason }
    : {};
}

export function foldTimeline(
  events: readonly JournalEvent[],
  runId: string,
): Timeline {
  const builder = new TimelineBuilder(runId);
  for (const event of events) builder.apply(event);
  return builder.timeline;
}
