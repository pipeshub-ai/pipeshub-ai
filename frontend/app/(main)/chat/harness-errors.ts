import { approvalAskRules, runErrorExtras } from '@/config';
import { i18n } from '@/lib/i18n';
import { harnessCodesOf } from '@/lib/contracts/harness/codes';
import type { NextActionKind, ToolErrorCode } from '@/lib/contracts/harness/generated/core/enums';
import type { RunCancelRequestedEvent } from '@/lib/contracts/harness/generated/core/events/run-cancel-requested';
import type { AnswerErrorCode } from '@/lib/contracts/harness/generated/ext/pipeshub/enums';
import { ERROR_CODES, type ErrorCode } from '@/lib/contracts/harness/generated/ext/pipeshub/errors';

/**
 * What a person reads when the durable harness refuses a turn or ends a run
 * (UX3, R1 F7, F4): the machine code the server sends, in the reader's
 * language, with what they can do next. Literal keys, so `i18n:keys` checks
 * every one.
 */

const NODE_PREFIXES = ['HTTP_AGENT_HARNESS_', 'HTTP_'] as const;

const bare = (code: string | null): string | null => {
  if (code === null) return null;
  const prefix = NODE_PREFIXES.find((p) => code.startsWith(p));
  return prefix ? code.slice(prefix.length) : code;
};

/** Model access and model account refusals (A1): the org's rules or the vendor's, never the user's doing. */
const MODEL_ACCESS: ReadonlySet<string> = new Set<string>([
  ...harnessCodesOf('model_access'),
  ...harnessCodesOf('model_account'),
]);

/**
 * The text for an admission the server refused, from its code (null: no code, e.g. a network failure). A quota
 * refusal names its rule in `reasonCode`: the cap on chats answering at once is waited out by an answer ending, not
 * by a minute passing, so it has its own words.
 */
export function admissionRefusalText(code: string | null, reasonCode?: string | null): string {
  const name = bare(code)?.replace(/^AGENT_HARNESS_/, '') ?? null;
  switch (name) {
    case 'QUOTA_EXCEEDED':
      if (reasonCode === 'active_runs_user') return i18n.t('chatStream.admission.quotaActiveUser');
      if (reasonCode === 'active_runs_org') return i18n.t('chatStream.admission.quotaActiveOrg');
      return i18n.t('chatStream.admission.quota');
    case 'VIEWER_READ_ONLY':
      return i18n.t('chatStream.admission.readOnly');
    case 'COLLABORATOR_TURNS_DISABLED':
      return i18n.t('chatStream.admission.collaboratorTurns');
    case 'SHARED_SESSION_ENGINE':
      return i18n.t('chatStream.admission.sharedSessionEngine');
    case 'RUN_ACTIVE':
      return i18n.t('chatStream.admission.runActive');
    case 'SESSION_ERASED':
      return i18n.t('chatStream.admission.erased');
    // An outage of the org's rules, not a refusal by them: waiting fixes it, picking another model does not.
    case 'ACCESS_POLICY_UNAVAILABLE':
      return i18n.t('chatStream.admission.policyUnavailable');
    case 'REGENERATE_NOT_LAST':
      return i18n.t('chatStream.admission.regenerateNotLast');
    case 'SESSION_HISTORY_TOO_LARGE':
      return i18n.t('chatStream.admission.historyTooLarge');
    case 'ENGINE_PROFILE_WIDENING':
    case 'ENGINE_PROFILE_INVALID':
      return i18n.t('chatStream.admission.engineProfile');
    case 'ENGINE_UNAVAILABLE':
      // ADR-SEC-01 D7: the image is there but its profile is signed by no key this deployment trusts.
      if (reasonCode === 'engine_profile_untrusted') return i18n.t('chatStream.admission.engineProfileUntrusted');
      return i18n.t('chatStream.admission.engineUnavailable');
    case 'ENGINE_SANDBOX_MISSING':
      return i18n.t('chatStream.admission.engineSandboxMissing');
    case 'RUN_CONFIG_UNAVAILABLE':
      return i18n.t('chatStream.admission.configUnavailable');
    // WP-64: today's loop would serve it and holds no gates; only an admin can turn the runtime on.
    case 'GATES_NEED_RUNTIME':
      return i18n.t('chatStream.admission.gatesNeedRuntime');
    case 'RUN_PLACEMENT_REFUSED':
      return i18n.t('chatStream.admission.placement');
    // G-41: the org lets only some groups use agents, this model or this engine; only an admin can add the person.
    case 'HARNESS_GROUP_REQUIRED':
      return i18n.t('chatStream.admission.harnessGroup');
    case 'MODEL_GROUP_REQUIRED':
      return i18n.t('chatStream.admission.modelGroup');
    case 'ENGINE_GROUP_REQUIRED':
      return i18n.t('chatStream.admission.engineGroup');
    case 'ACCOUNT_MISSING':
      return i18n.t('chatStream.admission.accountMissing');
    case 'REPOSITORY_NEEDS_ENGINE':
    case 'REPOSITORY_TOOLSET_MISSING':
    case 'REPOSITORY_NOT_GITHUB':
    case 'REPOSITORY_LOCKED':
      return repositoryRefusalText(code);
    // ADR-OPS-01 D4.2: an admin's control stops this conversation or agent; only they can resume it.
    case 'OPERATOR_STOPPED':
      return i18n.t('chatStream.admission.operatorStopped');
    case 'FORBIDDEN':
      return i18n.t('chatStream.admission.forbidden');
    case 'NOT_FOUND':
      return i18n.t('chatStream.admission.notFound');
    case null:
      return i18n.t('chatStream.admission.network');
    default:
      return name !== null && MODEL_ACCESS.has(name)
        ? i18n.t('chatStream.admission.modelAccess')
        : i18n.t('chatStream.admission.failed');
  }
}

/**
 * Why the server refused a chat's repository (WP-26), from `PUT …/repository` or a new chat's first message; any
 * other refusal reads as a plain failure.
 */
export function repositoryRefusalText(code: string | null): string {
  switch (bare(code)?.replace(/^AGENT_HARNESS_/, '')) {
    case 'REPOSITORY_NEEDS_ENGINE':
      return i18n.t('chatStream.admission.repositoryNeedsEngine');
    case 'REPOSITORY_TOOLSET_MISSING':
      return i18n.t('chatStream.admission.repositoryToolsetMissing');
    case 'REPOSITORY_NOT_GITHUB':
      return i18n.t('chatStream.admission.repositoryNotGithub');
    case 'REPOSITORY_LOCKED':
      return i18n.t('chatStream.admission.repositoryLocked');
    default:
      return i18n.t('chatStream.admission.repositoryFailed');
  }
}

/** The control that stopped a run (ADR-OPS-01 D5): its scope and reason, as `run.cancel_requested.stop` carries them. */
export type OperatorStopDetails = NonNullable<RunCancelRequestedEvent['data']['stop']>;

const STOP_SCOPES: readonly OperatorStopDetails['scope'][] = ['session', 'agent', 'org', 'engine_version', 'instance', 'egress'];
const STOP_REASONS: readonly OperatorStopDetails['reason'][] = ['security', 'cost', 'incident', 'maintenance', 'other'];

/**
 * A stop's details from whatever carried them (RUN_ERROR.details, a saved failure's errorStop): null without a known
 * scope; an unknown reason reads as `other`. Anything else they carry (an admin's note, ids) is dropped.
 */
export function operatorStopDetails(value: unknown): OperatorStopDetails | null {
  if (value === null || typeof value !== 'object') return null;
  const { scope, reason } = value as { scope?: unknown; reason?: unknown };
  const knownScope = STOP_SCOPES.find((s) => s === scope);
  if (knownScope === undefined) return null;
  return { scope: knownScope, reason: STOP_REASONS.find((r) => r === reason) ?? 'other' };
}

/**
 * ADR-OPS-01 D5: one sentence per scope an admin's control stopped; the reason only picks the engine version's
 * words, and the admin's note never reaches the reader. No details read as the generic sentence. The only place
 * these words are chosen: every caller parses with `operatorStopDetails` first.
 */
export function operatorStoppedText(details: OperatorStopDetails | null): string {
  switch (details?.scope) {
    case 'session':
      return i18n.t('chatStream.runError.operatorStoppedSession');
    case 'agent':
      return i18n.t('chatStream.runError.operatorStoppedAgent');
    case 'org':
      return i18n.t('chatStream.runError.operatorStoppedOrg');
    case 'engine_version':
      return details.reason === 'security'
        ? i18n.t('chatStream.runError.operatorStoppedEngineVersionSecurity')
        : i18n.t('chatStream.runError.operatorStoppedEngineVersion');
    case 'instance':
      return i18n.t('chatStream.runError.operatorStoppedInstance');
    case 'egress':
      return i18n.t('chatStream.runError.operatorStoppedEgress');
    default:
      return i18n.t('chatStream.runError.operatorStopped');
  }
}

/** Server labels as one phrase in the reader's language ("A, B, and C"); labels are the org's own names. */
function serverList(servers: readonly string[]): string {
  try {
    return new Intl.ListFormat(i18n.language, { style: 'long', type: 'conjunction' }).format(servers);
  } catch {
    return servers.join(', ');
  }
}

/** The memory limit an out-of-memory run met (`run.failed.memoryLimitMb`); null when its details do not say. */
export function memoryLimitOf(details: unknown): number | null {
  if (details === null || typeof details !== 'object') return null;
  const mb = (details as { memoryLimitMb?: unknown }).memoryLimitMb;
  return typeof mb === 'number' && Number.isInteger(mb) && mb > 0 ? mb : null;
}

/** A memory limit as the reader sees it: whole gigabytes as GB, anything else in MB. */
export function memoryLimitLabel(mb: number): string {
  return mb % 1024 === 0 ? `${mb / 1024} GB` : `${mb} MB`;
}

/**
 * The text for a durable run's ending code that has its own words; null keeps the server's message. `servers`: the
 * labels a `mcp_tool_withdrawn` ending names (RUN_ERROR.servers, a saved failure's errorServers). `details`: an
 * `operator_stopped` ending's control (RUN_ERROR.details, a saved failure's errorStop), parsed here.
 */
export function runErrorText(
  code: string | null,
  servers?: readonly string[] | null,
  details?: unknown,
): string | null {
  switch (code) {
    case 'operator_stopped':
      return operatorStoppedText(operatorStopDetails(details));
    case 'not_started':
      return i18n.t('chatStream.runError.notStarted');
    case 'no_compatible_worker':
      return i18n.t('chatStream.runError.noCompatibleWorker');
    case 'deadline_exceeded':
      return i18n.t('chatStream.runError.deadlineExceeded');
    case 'quarantined':
      return i18n.t('chatStream.runError.quarantined');
    // C4.10: the worker was lost and the agent's policy does not resume a run on its own.
    case 'worker_lost':
      return i18n.t('chatStream.runError.workerLost');
    case 'capability_lost':
      return i18n.t('chatStream.runError.capabilityLost');
    case 'model_no_longer_configured':
      return i18n.t('chatStream.runError.modelNoLongerConfigured');
    case 'mcp_unavailable':
      return i18n.t('chatStream.runError.mcpUnavailable');
    // WP-79/WP-84: a server that answers without a tool the run was using will not bring it back by waiting.
    case 'mcp_tool_withdrawn': {
      const named = (servers ?? []).filter((label) => typeof label === 'string' && label.trim() !== '');
      return named.length > 0
        ? i18n.t('chatStream.runError.mcpToolWithdrawn', { count: named.length, servers: serverList(named) })
        : i18n.t('chatStream.runError.mcpToolWithdrawnUnnamed');
    }
    // WP-77: the model provider, not PipesHub and not the person; waiting is what helps.
    case 'provider_unavailable':
      return i18n.t('chatStream.runError.providerUnavailable');
    case 'rate_limited':
      return i18n.t('chatStream.runError.rateLimited');
    case 'sandbox_unavailable':
      return i18n.t('chatStream.runError.sandboxUnavailable');
    // CH-09: file storage stayed away for the whole bounded wait; capacity of a dependency, so a retry can work.
    case 'store_unavailable':
      return i18n.t('chatStream.runError.storeUnavailable');
    // SEC-DOS-02: the engine flooded its run with events and was stopped.
    case 'event_flood':
      return i18n.t('chatStream.runError.eventFlood');
    // The coding engine could not reach PipesHub's tools on two attempts (SIGNFIX): a PipesHub-side failure.
    case 'engine_tools_unreachable':
      return i18n.t('chatStream.runError.engineToolsUnreachable');
    // C6.12 (WP-61): the run's sandboxes moved more to outside destinations than the deployment's egress cap allows.
    case 'egress_volume_exceeded':
      return i18n.t('chatStream.runError.egressVolumeExceeded');
    // WP-103: a model call PipesHub blocked as carrying what looked like a secret, before the task had gathered.
    case 'secret_blocked':
      return i18n.t('chatStream.runError.secretBlocked');
    // The engine cannot run on this deployment (EH1): only an admin can fix it, so it never says to try again.
    case 'binary_mismatch':
    case 'candidate_image':
    case 'engine_unavailable':
    case 'engine_image_missing':
    case 'engine_contract_unsupported':
    // WP103-FU-2: the egress proxy refused a compressed model call it cannot inspect; the engine's setup is the cause.
    case 'model_call_encoded':
      return i18n.t('chatStream.runError.engineUnavailable');
    // The engine's floor is gVisor and this server has no runsc: only an admin can install it. Each locale holds
    // the guide's address itself (QA17-U1 reads the words as catalogued); gvisor-notice.test.tsx keeps it current.
    case 'gvisor_unavailable':
      return i18n.t('chatStream.runError.gvisorUnavailable');
    // ADR-SEC-01: the run's image signature is missing or not trusted here; only an admin can fix it.
    case 'image_untrusted':
      return i18n.t('chatStream.runError.imageUntrusted');
    // R31GVISO-FU-3: the sandbox's memory limit ended the work (under gVisor the whole sandbox); the limit is named.
    case 'out_of_memory': {
      const limit = memoryLimitOf(details);
      return limit === null
        ? i18n.t('chatStream.runError.outOfMemory')
        : i18n.t('chatStream.runError.outOfMemoryLimit', { limit: memoryLimitLabel(limit) });
    }
    default:
      return code !== null && hasOwn(runErrorExtras, code) ? runErrorExtras[code].text() : null;
  }
}

/**
 * WP-64: a chat request refused before it streamed because today's loop would serve it ungated, in the reader's
 * language; null for any other failure, which keeps its own words.
 */
export function refusedTurnText(error: { code?: string } | null | undefined): string | null {
  const code = bare(error?.code ?? null)?.replace(/^AGENT_HARNESS_/, '');
  return code === 'GATES_NEED_RUNTIME' ? i18n.t('chatStream.admission.gatesNeedRuntime') : null;
}

/** A durable admission the server refused (or that could not reach it), with the reader's text. */
export class DurableAdmissionError extends Error {
  constructor(
    readonly code: string | null,
    readonly reasonCode: string | null = null,
    /** The refusal's HTTP status; undefined when no answer came. A card's send reads it (CARDFAIL). */
    readonly status?: number,
  ) {
    super(admissionRefusalText(code, reasonCode));
    this.name = 'DurableAdmissionError';
  }
}

/** What a person reads of a failed tool call (ERR-UX): the code's words naming the tool, never its detail. */
const TOOL_FAILURES: Readonly<Record<ToolErrorCode, (tool: string) => string>> = {
  unavailable: (tool) => i18n.t('chatStream.toolFailure.unavailable', { tool }),
  timeout: (tool) => i18n.t('chatStream.toolFailure.timeout', { tool }),
  rate_limited: (tool) => i18n.t('chatStream.toolFailure.rateLimited', { tool }),
  auth: (tool) => i18n.t('chatStream.toolFailure.auth', { tool }),
  not_found: (tool) => i18n.t('chatStream.toolFailure.notFound', { tool }),
  invalid_call: (tool) => i18n.t('chatStream.toolFailure.invalidCall', { tool }),
  failed: (tool) => i18n.t('chatStream.toolFailure.failed', { tool }),
};

const isToolErrorCode = (code: unknown): code is ToolErrorCode =>
  typeof code === 'string' && Object.prototype.hasOwnProperty.call(TOOL_FAILURES, code);

/**
 * A failed call's sentence: by its code (an older worker's failure without one reads as `failed`), and "nothing
 * was sent" for a write whose outcome is definite.
 */
export function toolFailureText(
  failure: { errorCode?: string | null; nothingSent?: boolean },
  tool: string,
): string {
  const code = isToolErrorCode(failure.errorCode) ? failure.errorCode : 'failed';
  const text = TOOL_FAILURES[code](tool);
  return failure.nothingSent === true ? `${text} ${i18n.t('chatStream.toolFailure.nothingSent')}` : text;
}

/** An error answer's words (QA17-O2), so neither the worker's English nor a provider's message reaches the reader. */
// This build's own codes; an edition's are `approvalAskRules.answerErrors`.
const ANSWER_ERRORS: Readonly<Partial<Record<AnswerErrorCode, () => string>>> = {
  content_filter: () => i18n.t('chatStream.answerError.contentFilter'),
  quota_exceeded: () => i18n.t('chatStream.answerError.quotaExceeded'),
  rate_limit: () => i18n.t('chatStream.answerError.rateLimit'),
  auth_error: () => i18n.t('chatStream.answerError.authError'),
  model_not_found: () => i18n.t('chatStream.answerError.modelNotFound'),
  request_too_large: () => i18n.t('chatStream.answerError.requestTooLarge'),
  invalid_request: () => i18n.t('chatStream.answerError.invalidRequest'),
  server_error: () => i18n.t('chatStream.answerError.serverError'),
  timeout: () => i18n.t('chatStream.answerError.timeout'),
  unknown: () => i18n.t('chatStream.answerError.unknown'),
  needs_reconciliation: () => i18n.t('chatStream.answerError.needsReconciliation'),
  deadline_exceeded: () => i18n.t('chatStream.answerError.deadlineExceeded'),
  approval_expired: () => i18n.t('chatStream.answerError.approvalExpired'),
  budget_exhausted: () => i18n.t('chatStream.answerError.budgetExhausted'),
  budget_insufficient: () => i18n.t('chatStream.answerError.budgetInsufficient'),
};

/** The reader's words for an error answer's code; null for an answer that is not one or a code we do not know. */
export function answerErrorText(code: unknown): string | null {
  if (typeof code !== 'string') return null;
  const own = Object.prototype.hasOwnProperty.call(ANSWER_ERRORS, code) ? ANSWER_ERRORS[code as AnswerErrorCode] : undefined;
  const words = own ?? (hasOwn(approvalAskRules.answerErrors, code) ? approvalAskRules.answerErrors[code].text : undefined);
  return words === undefined ? null : words();
}

/** G-48: an engine run queued for a free sandbox until `retryAt`; `now` is the reader's clock. */
export function sandboxWaitText(retryAt: unknown, now: number): string {
  const at = typeof retryAt === 'string' ? Date.parse(retryAt) : Number.NaN;
  const seconds = Number.isFinite(at) ? Math.ceil((at - now) / 1000) : 0;
  return seconds > 0 ? i18n.t('chatStream.sandboxWaitIn', { seconds }) : i18n.t('chatStream.sandboxWait');
}

/** The run waits for a rate-limited model provider (QA17-O3) until `retryAt`; `now` is the reader's clock. */
export function providerWaitText(retryAt: unknown, now: number): string {
  const at = typeof retryAt === 'string' ? Date.parse(retryAt) : Number.NaN;
  const seconds = Number.isFinite(at) ? Math.ceil((at - now) / 1000) : 0;
  return seconds > 0
    ? i18n.t('chatStream.providerWait', { seconds })
    : i18n.t('chatStream.providerWaitNow');
}

/**
 * The next action of a run ending and of an error answer (UX-06), as `vectors/error-next-actions.json` gives them;
 * `wp18-ux06-next-actions.test.ts` keeps the two equal. Admission refusals read the catalogue's own `nextAction`.
 */
const RUN_ERROR_NEXT: Readonly<Record<string, NextActionKind>> = {
  not_started: 'retry',
  no_compatible_worker: 'ask_admin',
  deadline_exceeded: 'retry',
  quarantined: 'retry',
  worker_lost: 'retry',
  capability_lost: 'retry',
  mcp_tool_withdrawn: 'retry',
  provider_unavailable: 'wait_then_retry',
  rate_limited: 'wait_then_retry',
  model_no_longer_configured: 'retry',
  binary_mismatch: 'ask_admin',
  candidate_image: 'ask_admin',
  engine_unavailable: 'ask_admin',
  engine_image_missing: 'ask_admin',
  engine_contract_unsupported: 'ask_admin',
  gvisor_unavailable: 'ask_admin',
  image_untrusted: 'ask_admin',
  sandbox_unavailable: 'wait_then_retry',
  store_unavailable: 'wait_then_retry',
  engine_tools_unreachable: 'retry',
  egress_volume_exceeded: 'change_request',
  secret_blocked: 'change_request',
  out_of_memory: 'change_request',
  event_flood: 'retry',
  operator_stopped: 'ask_admin',
};

const ANSWER_ERROR_NEXT: Readonly<Partial<Record<AnswerErrorCode, NextActionKind>>> = {
  content_filter: 'change_request',
  quota_exceeded: 'ask_admin',
  rate_limit: 'wait_then_retry',
  auth_error: 'ask_admin',
  model_not_found: 'ask_admin',
  request_too_large: 'change_request',
  invalid_request: 'ask_admin',
  server_error: 'wait_then_retry',
  timeout: 'retry',
  unknown: 'retry',
  needs_reconciliation: 'ask_admin',
  deadline_exceeded: 'retry',
  approval_expired: 'retry',
  budget_exhausted: 'ask_admin',
  budget_insufficient: 'ask_admin',
};

export const NEXT_ACTION_TABLES = { runErrors: RUN_ERROR_NEXT, answerErrors: ANSWER_ERROR_NEXT } as const;

/** Where a typed error the chat shows came from: a refused turn, a run's ending, or an error answer. */
export type ErrorSource = 'admission' | 'run' | 'answer';

/** A step one click takes (UX-06); every other kind is said in the error's own words and has no button. */
export type OneClickStep = 'retry' | 'wait_then_retry' | 'reload' | 'new_conversation';

const ONE_CLICK: ReadonlySet<NextActionKind> = new Set<NextActionKind>(['retry', 'wait_then_retry', 'reload', 'new_conversation']);

const hasOwn = <T extends object>(table: T, key: string): key is Extract<keyof T, string> =>
  Object.prototype.hasOwnProperty.call(table, key);

/** The contract's next action for a typed error; null for a code the contract does not name. */
export function nextActionKind(source: ErrorSource, code: string | null): NextActionKind | null {
  if (source === 'admission') {
    const name = bare(code);
    // No code is a turn that never reached the server, and an unnamed refusal reads as the generic one: both retry.
    if (name === null) return 'retry';
    return hasOwn(ERROR_CODES, name) ? ERROR_CODES[name as ErrorCode].nextAction.kind : 'retry';
  }
  if (code === null) return null;
  const table: Readonly<Record<string, NextActionKind | undefined>> = source === 'run' ? RUN_ERROR_NEXT : ANSWER_ERROR_NEXT;
  if (hasOwn(table, code)) return table[code] ?? null;
  if (source === 'run') return hasOwn(runErrorExtras, code) ? runErrorExtras[code].next : null;
  return hasOwn(approvalAskRules.answerErrors, code) ? approvalAskRules.answerErrors[code].next : null;
}

/** The button a typed error offers, or null when its next step is not one click (ask an admin, change the ask). */
export function oneClickStep(source: ErrorSource, code: string | null): OneClickStep | null {
  const kind = nextActionKind(source, code);
  return kind !== null && ONE_CLICK.has(kind) ? (kind as OneClickStep) : null;
}
