import type { RunAdmissionResult } from '../../domain/admission';
import {
  HarnessEngineProfileRefusedError,
  HarnessModelAccessRefusedError,
  HarnessQuotaExceededError,
} from '../../domain/errors';
import { isClaimChecked } from '../../domain/journal';
import type {
  HarnessRolloutDecision,
  HarnessRolloutReason,
} from '../../domain/rollout';
import type { SessionImportSummary } from '../../domain/session-import';
import { inputQueuedEvent } from '../../domain/steering';
import { harnessLogMeta, HarnessLogger } from '../harness-log';
import { admissionSpan, HarnessTracer } from '../harness-tracing';
import type { AdmissionTiming, SubmitDurableRun } from './context';
import { AdmissionRefusal, isKnownRefusal } from './refusals';

/** The correlation fields every admission line carries (ground rule 9). */
export interface AdmissionLogFields {
  orgId: string;
  sessionId: string | undefined;
  actorId: string;
  agentKey: string | undefined;
  via: NonNullable<SubmitDurableRun['via']>;
  surface: NonNullable<SubmitDurableRun['surface']>;
  assistant: boolean;
  chatMode: string;
  /** Present only when the turn asked for the durable harness (ASSIST1). */
  durableRequested?: true;
  /** Present only when the turn asked for the classic path (CTL-7). */
  legacyRequested?: true;
}

/** The client's mode as a machine name, never free text in a log line. */
export const loggableChatMode = (value: string | undefined): string => {
  const mode = (value ?? '').trim().toLowerCase();
  return /^[a-z][a-z_:]{0,31}$/.test(mode) ? mode : 'other';
};

/** What a line says of the turn itself, whoever sent it. */
const turnLogFields = (turn: {
  sessionId?: string | undefined;
  agentKey?: string | undefined;
  via?: SubmitDurableRun['via'];
  surface?: SubmitDurableRun['surface'];
  chatMode: string | undefined;
}): Omit<AdmissionLogFields, 'orgId' | 'actorId'> => ({
  sessionId: turn.sessionId,
  agentKey: turn.agentKey,
  via: turn.via ?? 'session',
  surface: turn.surface ?? 'web',
  assistant: turn.agentKey === undefined,
  chatMode: loggableChatMode(turn.chatMode),
});

export const admissionLogFields = (
  request: Pick<
    SubmitDurableRun,
    | 'orgId'
    | 'sessionId'
    | 'actorId'
    | 'agentKey'
    | 'via'
    | 'surface'
    | 'durableRequested'
    | 'legacyRequested'
  > & { chatMode: string | undefined },
): AdmissionLogFields => ({
  orgId: request.orgId,
  actorId: request.actorId,
  ...turnLogFields(request),
  ...(request.durableRequested === true ? { durableRequested: true } : {}),
  ...(request.legacyRequested === true ? { legacyRequested: true } : {}),
});

/**
 * Why a chat turn runs on today's loop: the rollout's reason, or the route's
 * own when admission was never asked or could not answer.
 */
export type LegacyTurnReason =
  | HarnessRolloutReason
  /** The body is one the durable route cannot serve as sent. */
  | 'surface_request_not_supported'
  /** Admission could not decide and wrote nothing (UX3 AR-2). */
  | 'admission_unavailable';

/**
 * A turn the route leaves to today's loop before admission answers. The
 * identity is absent where only a store read would resolve it (the Slack
 * bot's service credential with durable runs off).
 */
export interface DeclinedTurn {
  orgId?: string;
  actorId?: string;
  agentKey?: string;
  sessionId?: string;
  via: NonNullable<SubmitDurableRun['via']>;
  surface: NonNullable<SubmitDurableRun['surface']>;
  chatMode: string | undefined;
  requestId?: string;
}

export type LegacyTurnFields = Omit<AdmissionLogFields, 'orgId' | 'actorId'> & {
  orgId?: string | undefined;
  actorId?: string | undefined;
  requestId?: string | undefined;
};

export const declinedTurnFields = (turn: DeclinedTurn): LegacyTurnFields => ({
  orgId: turn.orgId,
  actorId: turn.actorId,
  ...turnLogFields(turn),
  requestId: turn.requestId,
});

/**
 * The one `admission.legacy` line of a turn (QA6-F11): every path that leaves
 * a turn to today's loop writes it here, so the lines differ only in `reason`.
 */
export function logLegacyTurn(
  logger: HarnessLogger,
  fields: LegacyTurnFields,
  reason: LegacyTurnReason,
): void {
  logger.info(
    'admission.legacy',
    harnessLogMeta('admission.legacy', { ...fields, reason }),
  );
}

/** What a submission came to, as the log line names it. */
export type LoggedOutcome =
  | { route: 'durable'; admission: RunAdmissionResult }
  | { route: 'legacy'; rollout: HarnessRolloutDecision };

/**
 * Admission's log lines and root span (R2 3.4: logging out of the decision).
 * One line per submission, with its reason code; never the input or the
 * caller's credential, and a log line never fails an admission.
 */
export class AdmissionLog {
  constructor(
    private readonly logger: HarnessLogger,
    private readonly tracer: HarnessTracer,
  ) {}

  /** A refusal (warning) or a failure (error), with what the refusal names. */
  refused(fields: AdmissionLogFields, refused: AdmissionRefusal): void {
    const error = refused.error;
    const quota =
      error instanceof HarnessQuotaExceededError
        ? {
            scope: error.scope,
            quota: error.quota,
            retryAfterS: error.retryAfterS,
          }
        : {};
    const profile =
      error instanceof HarnessEngineProfileRefusedError
        ? {
            ...(error.dimension !== undefined
              ? { dimension: error.dimension }
              : {}),
            ...(error.layer !== undefined ? { layer: error.layer } : {}),
          }
        : {};
    const access =
      error instanceof HarnessModelAccessRefusedError && error.rule !== null
        ? { rule: error.rule }
        : {};
    const known = isKnownRefusal(error);
    const meta = harnessLogMeta(
      known ? 'admission.refused' : 'admission.failed',
      {
        ...fields,
        reason: refused.reason,
        ...quota,
        ...profile,
        ...access,
        errorType: error instanceof Error ? error.name : typeof error,
      },
    );
    if (known) this.logger.warn('admission.refused', meta);
    else this.logger.error('admission.failed', meta);
  }

  outcome(
    fields: AdmissionLogFields,
    request: SubmitDurableRun,
    outcome: LoggedOutcome,
    timing: AdmissionTiming,
    took: { ms: number; startedAt: number },
  ): void {
    if (outcome.route === 'legacy') {
      logLegacyTurn(this.logger, fields, outcome.rollout.reason);
      return;
    }
    const { run, created, queued, imported, nextRun } =
      outcome.admission as Partial<RunAdmissionResult>;
    const accepted = {
      ...fields,
      sessionId: run?.sessionId ?? fields.sessionId,
      runId: run?.runId,
      replayed: created === undefined ? undefined : !created,
      ms: took.ms,
      ...timing,
    };
    if (nextRun !== undefined) {
      this.logger.info(
        'input.next_run_queued',
        harnessLogMeta('input.next_run_queued', {
          ...accepted,
          nextRunId: nextRun.id,
          nextRunRunId: nextRun.runId,
          position: nextRun.position,
        }),
      );
      return;
    }
    if (queued !== undefined) {
      this.logger.info(
        'input.queued',
        harnessLogMeta('input.queued', {
          ...accepted,
          messageSeq: queued.messageSeq,
          route: fields.assistant ? 'chat' : 'agent',
          attachments: request.input.attachments.length,
          records: request.input.recordIds.length,
          claimChecked: isClaimChecked(
            inputQueuedEvent(queued.messageSeq, request.input, request.actorId)
              .data,
          ),
        }),
      );
      return;
    }
    this.logger.info(
      'admission.accepted',
      harnessLogMeta('admission.accepted', {
        ...accepted,
        profile: run?.profile,
        harnessMajor: run?.harnessMajor,
        sessionRole: run?.sessionRole ?? null,
      }),
    );
    if (created === true && imported !== undefined) {
      this.imported(accepted, imported);
    }
    if (this.tracer.enabled && run !== undefined) {
      this.tracer.record(
        admissionSpan({
          orgId: run.orgId,
          sessionId: run.sessionId,
          runId: run.runId,
          startMs: took.startedAt,
          endMs: Date.now(),
          profile: run.profile,
          harnessMajor: run.harnessMajor,
          ...(created !== undefined ? { replayed: !created } : {}),
          ...(request.agentKey !== undefined
            ? { agentKey: request.agentKey }
            : {}),
        }),
      );
    }
  }

  private imported(
    fields: {
      orgId: string;
      sessionId?: string;
      runId?: string;
      actorId: string;
    },
    imported: SessionImportSummary,
  ): void {
    const ids = {
      orgId: fields.orgId,
      sessionId: fields.sessionId,
      runId: fields.runId,
      actorId: fields.actorId,
    };
    if (imported.mismatch !== undefined) {
      this.logger.warn(
        'session.import_mismatch',
        harnessLogMeta('session.import_mismatch', {
          ...ids,
          reason: imported.mismatch,
          choice: 'reimport',
          upToMessageSeq: imported.upToMessageSeq,
        }),
      );
    }
    this.logger.info(
      'session.imported',
      harnessLogMeta('session.imported', {
        ...ids,
        upToMessageSeq: imported.upToMessageSeq,
        messages: imported.messages,
        digest: imported.digest,
        replacesHistory: imported.replacesHistory,
        sealed: imported.sealed,
      }),
    );
  }
}
