import { RunKeyDigest } from '../domain/run-key-digest';
import { RunSurface } from '../domain/admission';
import type { RunVia } from '../domain/run-spec';
import { HarnessConflictError, HarnessNotFoundError } from '../domain/errors';
import type { HarnessRun } from '../domain/execution';
import { conversationLink } from '../domain/run-end-notice';
import { AUDIENCE_VIEWS } from '../projection/audiences';
import {
  AGUI,
  AGUI_CUSTOM,
  Audience,
  ProjectedFrame,
} from '../projection/frames';
import {
  FrameSink,
  RunStreamService,
  StreamHandle,
} from '../projection/stream-service';
import { RunAdmissionService, SubmitDurableRun } from './run-admission-service';
import {
  DeclinedTurn,
  declinedTurnFields,
  LegacyTurnReason,
  logLegacyTurn,
} from './admission/admission-log';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import { SessionRunService } from './session-run-service';
import type { FrameVersion } from '../projection/frame-version';
import type { ResolvedRoute } from '../ports/routing-decider';
import type { SessionExecutionScope } from '../ports/chat-session-repository';

export interface ChatTurnRequest extends SubmitDurableRun {
  surface: RunSurface;
  requestId?: string;
  /** The frames' revision the web app asked for (WP-100); only the stream reads it, never admission. */
  frames?: FrameVersion;
}

/** The idempotency key of a turn the client named with its own run id (LAT1). */
export const turnIdempotencyKey = (clientRunId: string): string =>
  `turn:${clientRunId}`;

/** A legacy cancel, `POST …/cancel {runId}`, as the durable path reads it (LAT2). */
export interface ChatTurnCancel {
  orgId: string;
  actorId: string;
  agentKey?: string;
  sessionId: string;
  /** The run id the client sent its turn with, or the durable run's own. */
  runId: string;
  via: RunVia;
  requestId?: string;
}

export type ChatTurnStart =
  | {
      route: 'legacy';
      /** What the control plane resolved the turn on, when it decided (CPD-10): the proxied request's header is made of it. */
      resolved?: ResolvedRoute;
    }
  | {
      route: 'durable';
      audience: Audience;
      /** The conversation the run is in (a new turn may have made it). */
      sessionId: string;
      /** Streams the run on `sink`; closing it never stops the run. */
      open(sink: FrameSink): StreamHandle;
    }
  | {
      /** The turn joined the session's running run (steering, T2). */
      route: 'queued';
      /** The whole reply: the running run's own stream carries its answer. */
      frames: ProjectedFrame[];
    };

/** What a surface shows for a message that joined the running task. */
export const QUEUED_REPLY = 'Added to the running task.';

/** What a surface shows for a message waiting for someone else's task to end (SS2). */
export const NEXT_RUN_REPLY =
  'Queued: it starts when the task running in this conversation ends.';

/**
 * R35SLACK-FU-3 (ADR-OWD-09 D-SLACK-ASK 5): a message to a run parked at a card, from a surface that cannot show the
 * card. It is taken as steering and read once the run goes on, so the reply says what still waits, where to decide it,
 * and that the message will be read then; "Added to the running task." alone reads as if the run were working on it.
 */
export const QUEUED_WAITING_REPLY =
  'This is still waiting for an approval. Your message is added and will be read once it goes on.';

/** What a parked run waits on, as a hand-off names it (the projector's `waitingOn`). */
export interface WaitingOn {
  toolName: string;
  displayName?: string;
}

/** The calls a run waits on, read for a reply that hands off; empty when they cannot be read. */
export type WaitingOnReader = (run: HarnessRun) => Promise<WaitingOn[]>;

/** A queued reply that hands off: the run is parked at a card the surface cannot show. */
export interface QueuedHandOff {
  link: string;
  waitingOn: WaitingOn[];
}

/**
 * The reply to a steered surface turn, in the shape a finished turn has, so
 * the Slack bot posts it and the MCP tool returns it as its answer (neither
 * reads a new frame). The answer follows on the first turn's stream. With
 * `handOff`, the reply carries the hand-off's meta (`awaiting`, `link`,
 * `waitingOn`) and `queued`, so the bot words it as the run's hand-off does.
 */
export function queuedReplyFrames(
  sessionId: string,
  runId: string,
  messageSeq: number,
  reply: string = QUEUED_REPLY,
  handOff?: QueuedHandOff,
): ProjectedFrame[] {
  const id = (k: number): string =>
    `q:${runId}:${String(messageSeq)}:${String(k)}`;
  return [
    {
      id: id(1),
      frame: {
        type: AGUI.CUSTOM,
        name: AGUI_CUSTOM.CONVERSATION_CREATED,
        value: { conversationId: sessionId },
      },
    },
    {
      id: id(2),
      // Only what every surface audience admits: no run id reaches Slack or MCP.
      frame: {
        type: AGUI.RUN_FINISHED,
        result: {
          conversation: {
            _id: sessionId,
            status: handOff !== undefined ? 'Waiting' : 'Inprogress',
            messages: [
              {
                messageType: 'bot_response',
                content: reply,
                citations: [],
              },
            ],
          },
          ...(handOff !== undefined
            ? {
                meta: {
                  awaiting: 'approval',
                  queued: true,
                  link: handOff.link,
                  waitingOn: handOff.waitingOn,
                },
              }
            : {}),
        },
      },
    },
  ];
}

/** A run stopped on a person's decision: its own card, or one its live child put to the person. */
export const parkedAtCard = (run: HarnessRun): boolean =>
  run.status === 'waiting_approval' || (run.openInteractions?.length ?? 0) > 0;

/** What each surface's legacy route rendered (C-5, C-20); the web app and API clients see the owner's view. */
const SURFACE_AUDIENCE: Readonly<Record<RunSurface, Audience>> = {
  web: 'owner',
  slack: 'slack',
  mcp: 'external',
};

/** Why the route leaves a turn to legacy without an admission answer. */
export type ChatTurnDeclineReason = Extract<
  LegacyTurnReason,
  | 'infrastructure_unavailable'
  | 'surface_request_not_supported'
  | 'admission_unavailable'
>;

export interface ChatTurnServiceOptions {
  admissions: RunAdmissionService;
  runs: SessionRunService;
  streams: RunStreamService;
  logger?: HarnessLogger;
  /** How admission stored the turn's key (QA-15F); absent: a stored key is compared as sent. */
  runKeys?: RunKeyDigest;
  /** R35SLACK-FU-3: what a parked run waits on, for a surface's queued reply; absent, the reply names none. */
  waitingOn?: WaitingOnReader;
}

/**
 * One chat turn on the existing chat routes (LAT1, FR-40): the web app, an
 * API or PAT client, Slack or an MCP client. Admitted like any other run,
 * then streamed back in the same response to the surface's audience, so each
 * reads what its legacy route gave it. When routing says legacy, nothing is
 * written and the caller serves the legacy route in the same request.
 */
export class ChatTurnService {
  private readonly admissions: RunAdmissionService;
  private readonly runs: SessionRunService;
  private readonly streams: RunStreamService;
  private readonly logger: HarnessLogger;
  private readonly runKeys: RunKeyDigest | undefined;
  private readonly waitingOn: WaitingOnReader | undefined;

  constructor(options: ChatTurnServiceOptions) {
    this.admissions = options.admissions;
    this.runs = options.runs;
    this.streams = options.streams;
    this.logger = options.logger ?? silentHarnessLogger;
    this.runKeys = options.runKeys;
    this.waitingOn = options.waitingOn;
  }

  /**
   * WP-64: a message here would answer a gated run's question, which today's loop must not serve. False for a caller
   * who may not answer in the session (SEC-C-12), as for a session that does not exist.
   */
  answersGatedQuestion(scope: SessionExecutionScope): Promise<boolean> {
    return this.admissions.answersGatedQuestion(scope);
  }

  /**
   * D-SLACK-ASK 5: a steered message to a parked run, from an audience that cannot show its card, is answered with the
   * hand-off (what waits, the conversation's link). Reading what waits never refuses the message: it is already queued.
   */
  private async queuedHandOff(
    surface: RunSurface,
    run: HarnessRun,
  ): Promise<QueuedHandOff | undefined> {
    const audience = SURFACE_AUDIENCE[surface];
    if (
      audience === 'owner' ||
      !AUDIENCE_VIEWS[audience].handsOff ||
      !parkedAtCard(run)
    ) {
      return undefined;
    }
    let waitingOn: WaitingOn[] = [];
    try {
      waitingOn = (await this.waitingOn?.(run)) ?? [];
    } catch (error) {
      this.logger.warn(
        'chat_turn.waiting_on_unread',
        harnessLogMeta('chat_turn.waiting_on_unread', {
          runId: run.runId,
          error: error instanceof Error ? error.name : 'unknown',
        }),
      );
    }
    return { link: conversationLink(run.sessionId, run.agentKey), waitingOn };
  }

  /** Throws the admission's refusals; the transport maps them to HTTP errors. */
  async start(turn: ChatTurnRequest): Promise<ChatTurnStart> {
    // The frames' revision is the stream's alone: admission never sees it.
    const { frames, ...request } = turn;
    const outcome = await this.admissions.submit(request);
    if (outcome.route === 'legacy') {
      return {
        route: 'legacy',
        ...(outcome.resolved !== undefined
          ? { resolved: outcome.resolved }
          : {}),
      };
    }
    const admitted = outcome.admission.run;
    const queued = outcome.admission.queued;
    const nextRun = outcome.admission.nextRun;
    if (nextRun !== undefined) {
      return {
        route: 'queued',
        frames: queuedReplyFrames(
          admitted.sessionId,
          nextRun.runId,
          0,
          NEXT_RUN_REPLY,
        ),
      };
    }
    if (queued !== undefined) {
      const handOff = await this.queuedHandOff(request.surface, admitted);
      return {
        route: 'queued',
        frames: queuedReplyFrames(
          admitted.sessionId,
          admitted.runId,
          queued.messageSeq,
          handOff !== undefined ? QUEUED_WAITING_REPLY : QUEUED_REPLY,
          handOff,
        ),
      };
    }
    const { run, access } = await this.runs.admittedStreamAccess(
      {
        orgId: request.orgId,
        actorId: request.actorId,
        sessionId: admitted.sessionId,
        ...(request.agentKey !== undefined
          ? { agentKey: request.agentKey }
          : {}),
      },
      outcome.admission,
    );
    // The admitting actor owns the session; anything else gets the narrower view.
    const audience: Audience =
      access.audience === 'owner'
        ? SURFACE_AUDIENCE[request.surface]
        : 'shared';
    return {
      route: 'durable',
      audience,
      sessionId: run.sessionId,
      open: (sink) =>
        this.streams.open(
          {
            run,
            audience,
            ...(access.title !== undefined ? { title: access.title } : {}),
            ...(request.requestId !== undefined
              ? { requestId: request.requestId }
              : {}),
            ...(request.authorization !== undefined
              ? { authorization: request.authorization }
              : {}),
            ...(frames !== undefined ? { frames } : {}),
          },
          sink,
        ),
    };
  }

  /** Admission stores the key digested (QA-15F); a run an older build admitted holds it as sent. */
  private storesTurnKey(
    run: { idempotencyKey: string },
    orgId: string,
    clientRunId: string,
  ): boolean {
    const key = turnIdempotencyKey(clientRunId);
    return (
      run.idempotencyKey === key ||
      (this.runKeys !== undefined &&
        this.runKeys.matches(orgId, key, run.idempotencyKey))
    );
  }

  /**
   * Stops the session's running durable turn that `runId` names, by the
   * client's run id or by the run's own. Null when it names none the caller
   * may stop: today's cancel answers then. False when the run ended before
   * the cancel landed.
   */
  async cancel(request: ChatTurnCancel): Promise<boolean | null> {
    const scope = {
      orgId: request.orgId,
      actorId: request.actorId,
      sessionId: request.sessionId,
      ...(request.agentKey !== undefined ? { agentKey: request.agentKey } : {}),
    };
    let run;
    try {
      run = await this.runs.activeRun(scope);
    } catch (error) {
      // Today's cancel still stops a legacy turn of this conversation.
      this.logger.warn(
        'chat_turn.cancel_lookup_failed',
        harnessLogMeta('chat_turn.cancel_lookup_failed', {
          ...scope,
          requestId: request.requestId,
          errorType: error instanceof Error ? error.name : typeof error,
          choice: 'legacy_cancel',
        }),
      );
      return null;
    }
    const named =
      run !== null &&
      run !== undefined &&
      (run.runId === request.runId ||
        this.storesTurnKey(run, request.orgId, request.runId));
    if (run === null || run === undefined || !named) return null;
    // Only its own actor stops a run, and only on its own agent's route.
    if (run.actorId !== request.actorId || run.agentKey !== request.agentKey) {
      return null;
    }
    try {
      // The web app's Stop uses the same key, so the two never double the command.
      await this.runs.control({
        ...scope,
        runId: run.runId,
        idempotencyKey: `stop-${run.runId}`,
        action: 'cancel',
      });
    } catch (error) {
      if (
        error instanceof HarnessConflictError ||
        error instanceof HarnessNotFoundError
      ) {
        return false;
      }
      throw error;
    }
    this.logger.info(
      'chat_turn.cancelled',
      harnessLogMeta('chat_turn.cancelled', {
        ...scope,
        runId: run.runId,
        via: request.via,
        requestId: request.requestId,
      }),
    );
    return true;
  }

  /** The route leaves the turn to today's loop: logged as a rollout decision is. */
  declined(turn: DeclinedTurn, reason: ChatTurnDeclineReason): void {
    logLegacyTurn(this.logger, declinedTurnFields(turn), reason);
  }
}
