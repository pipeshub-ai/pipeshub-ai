import { FrameVersion, servesOutcomeFrames } from './frame-version';
import { CostPush } from './cost-push';
import type { RunSpendReader } from '../application/run-spend';
import { HarnessRun } from '../domain/execution';
import { dependencyHealth } from '../ops/dependency-health';
import { JournalEvent } from '../domain/journal';
import { JournalService } from '../application/journal-service';
import {
  harnessLogMeta,
  HarnessLogger,
  runLogFields,
  silentHarnessLogger,
} from '../application/harness-log';
import { SharedCitationService } from '../application/shared-citations';
import { SharedArtifactService } from '../application/shared-artifacts';
import { ConversationReader } from '../ports/conversation-reader';
import { JournalStore } from '../ports/journal-store';
import { JournalTail, JournalTailSubscription } from '../ports/journal-tail';
import { LiveBus, LiveBusSubscription } from '../ports/live-bus';
import {
  Audience,
  formatSseFrame,
  LiveDelta,
  ProjectedFrame,
  SSE_HEARTBEAT,
} from './frames';
import {
  ArtifactAccess,
  cardArtifactId,
  markerArtifactIds,
  messageArtifactIds,
} from './artifact-access';
import { CitationAccess } from './citation-access';
import {
  CheckedSource,
  checkedSources,
  messageSources,
  sourceKey,
} from './citation-source';
import { ChildInteractions, StateFrame } from './child-interactions';
import { planReplay, ReplayOptions } from './replay';
import { RunProjector } from './run-projector';
import { AUDIENCE_VIEWS } from './audiences';
import { conversationLink } from '../domain/run-end-notice';
import {
  RunJournalFollowing,
  RunJournalReader,
} from '../application/run-journal-reader';
import {
  parseStreamId,
  positionalId,
  StreamCursor,
  StreamPosition,
  withChildState,
} from './stream-id';
import {
  detachedInterval,
  spawnDetached,
} from '../../../libs/context/orgContext';

/**
 * One SSE subscription to a durable run (REPORT 6.2.4, 7.1): replay what the
 * journal and the LiveBus already hold from the client's `Last-Event-ID`,
 * then follow both until the run ends. Closing the stream only unsubscribes;
 * the run goes on (REPORT 8 intentional change).
 *
 * The journal is the truth: every frame can be rebuilt from it. The LiveBus
 * only makes a message appear token by token, so when it is down, when the
 * client cannot keep up, or when its buffer expired, the stream falls back
 * to message granularity from the journal without losing or repeating
 * anything the client has (L-2, L-4, L-5).
 */

const HEARTBEAT_MS = 15_000;
const ARTIFACT_PRESENTED = 'x.pipeshub.artifact.presented';

const TERMINAL_EVENTS = new Set([
  'run.completed',
  'run.cancelled',
  'run.failed',
  'run.quarantined',
]);

export interface FrameSink {
  /** False when the client is not keeping up (Node's socket buffer is full). */
  write(chunk: string): boolean;
  end(): void;
  onDrain(listener: () => void): void;
  /** Bytes the socket has not sent yet. */
  readonly pending: number;
}

export interface StreamRequest {
  run: HarnessRun;
  audience: Audience;
  title?: string;
  lastEventId?: string;
  requestId?: string;
  /** The viewer's own bearer credential: a shared view checks their access with it. */
  authorization?: string;
  /** The frames' revision the client asked for (`X-PipesHub-Frames`, WP-100); absent: the legacy frames. */
  frames?: FrameVersion;
}

export interface StreamHandle {
  close(reason?: string): void;
  /** Settles when the stream has ended (run finished or closed). */
  readonly done: Promise<void>;
}

type JournalReader = Pick<JournalStore<unknown>, 'readRun'>;

export interface RunStreamServiceOptions {
  journal: JournalReader;
  journals: JournalService;
  tail: JournalTail;
  conversations: ConversationReader;
  liveBus: LiveBus | null;
  logger?: HarnessLogger;
  heartbeatMs?: number;
  sharedCitations?: SharedCitationService;
  sharedArtifacts?: SharedArtifactService;
  /** The Node's reader of run journals, shared with its commits; its own when absent. */
  runJournal?: RunJournalReader;
  /** The ledger's reading of a run's spend, pushed to an owner reading the outcome frames (WP-100); absent: none. */
  spend?: RunSpendReader;
  /** The least time between two pushed spend reads (`COST_PUSH_MIN_MS`). */
  costPushMs?: number;
}

export class RunStreamService {
  private readonly journal: JournalReader;
  private readonly journals: JournalService;
  private readonly tail: JournalTail;
  private readonly conversations: ConversationReader;
  private readonly liveBus: LiveBus | null;
  private readonly logger: HarnessLogger;
  private readonly heartbeatMs: number;
  private readonly sharedCitations: SharedCitationService;
  private readonly sharedArtifacts: SharedArtifactService;
  private readonly runJournal: RunJournalReader;
  private readonly spend: RunSpendReader | null;
  private readonly costPushMs: number | undefined;

  constructor(options: RunStreamServiceOptions) {
    this.journal = options.journal;
    this.journals = options.journals;
    this.tail = options.tail;
    this.conversations = options.conversations;
    this.liveBus = options.liveBus;
    this.logger = options.logger ?? silentHarnessLogger;
    this.heartbeatMs = options.heartbeatMs ?? HEARTBEAT_MS;
    this.spend = options.spend ?? null;
    this.costPushMs = options.costPushMs;
    this.sharedCitations =
      options.sharedCitations ?? new SharedCitationService(null, this.logger);
    this.sharedArtifacts =
      options.sharedArtifacts ?? new SharedArtifactService(null, this.logger);
    this.runJournal =
      options.runJournal ??
      new RunJournalReader({
        journal: this.journal,
        journals: this.journals,
        logger: this.logger,
      });
  }

  open(request: StreamRequest, sink: FrameSink): StreamHandle {
    return new RunStream(this, request, sink).start();
  }

  /** @internal */
  deps(): {
    journal: JournalReader;
    journals: JournalService;
    tail: JournalTail;
    conversations: ConversationReader;
    liveBus: LiveBus | null;
    logger: HarnessLogger;
    heartbeatMs: number;
    sharedCitations: SharedCitationService;
    sharedArtifacts: SharedArtifactService;
    runJournal: RunJournalReader;
    spend: RunSpendReader | null;
    costPushMs: number | undefined;
  } {
    return {
      journal: this.journal,
      journals: this.journals,
      tail: this.tail,
      conversations: this.conversations,
      liveBus: this.liveBus,
      logger: this.logger,
      heartbeatMs: this.heartbeatMs,
      sharedCitations: this.sharedCitations,
      sharedArtifacts: this.sharedArtifacts,
      runJournal: this.runJournal,
      spend: this.spend,
      costPushMs: this.costPushMs,
    };
  }
}

class RunStream implements StreamHandle {
  private readonly deps: ReturnType<RunStreamService['deps']>;
  private projector!: RunProjector;
  private chain: Promise<void> = Promise.resolve();
  private tailSub: JournalTailSubscription | null = null;
  /** What this Node's own reads of the run bring, handed over as they answer. */
  private journalSub: RunJournalFollowing | null = null;
  /** The owner's view of its live children's interactions (UX3); null for other audiences. */
  private children: ChildInteractions | null = null;
  private readonly childSubs = new Map<string, JournalTailSubscription>();
  private busSub: LiveBusSubscription | null = null;
  /** The run's spend pushed as the ledger moves (WP-100); null unless the owner reads the outcome frames. */
  private cost: CostPush | null = null;
  private heartbeat: NodeJS.Timeout | null = null;
  /** Set while an ask a held grant covers waits for Node's answer (WP115-FU-2). */
  private heldTimer: NodeJS.Timeout | null = null;
  private closed = false;
  private congested = false;
  /** A rebuild after the client fell behind (L-4) is queued. */
  private resyncing = false;
  private frames = 0;
  /** The id of the last frame this client has: an `a:` end is placed after it. */
  private lastSentId: string | null = null;
  /** Frames stating a child's card since the last positional frame: their place in its id. */
  private stated = 0;
  private readonly droppedFences = new Set<number>();
  /** A shared view's citation access, decided once per subscription (C-6). */
  private citations: CitationAccess | null = null;
  /** Sources the run's journal cited, by `sourceKey`, until the access is decided. */
  private readonly citedSources = new Map<string, CheckedSource>();
  /** A shared view's artifact access (SS0), widened as new artifacts appear. */
  private artifacts = ArtifactAccess.hideAll();
  /** Artifacts already decided for this subscription, so each is asked about once. */
  private readonly decidedArtifacts = new Set<string>();
  /** The finished conversation a shared view read to decide its access. */
  private finished: { conversation: Record<string, unknown> | null } | null =
    null;
  private resolveDone!: () => void;
  readonly done: Promise<void>;

  constructor(
    service: RunStreamService,
    private readonly request: StreamRequest,
    private readonly sink: FrameSink,
  ) {
    this.deps = service.deps();
    this.done = new Promise((resolve) => {
      this.resolveDone = resolve;
    });
  }

  private fields(): Record<string, string | number | null> {
    return {
      ...runLogFields(this.request.run),
      requestId: this.request.requestId ?? null,
      audience: this.request.audience,
    };
  }

  start(): StreamHandle {
    this.enqueue(() => this.catchUp());
    this.heartbeat = detachedInterval(() => {
      if (!this.closed) this.sink.write(SSE_HEARTBEAT);
    }, this.deps.heartbeatMs);
    this.sink.onDrain(() => {
      this.congested = false;
    });
    return this;
  }

  close(reason = 'client_closed'): void {
    if (this.closed) return;
    this.closed = true;
    this.tailSub?.close();
    this.journalSub?.close();
    this.busSub?.close();
    this.cost?.close();
    for (const sub of this.childSubs.values()) sub.close();
    this.childSubs.clear();
    if (this.heartbeat !== null) clearInterval(this.heartbeat);
    if (this.heldTimer !== null) clearTimeout(this.heldTimer);
    this.deps.logger.info(
      'projection.unsubscribe',
      harnessLogMeta('projection.unsubscribe', {
        ...this.fields(),
        reason,
        frames: this.frames,
        terminal: this.terminalKind(),
      }),
    );
    this.resolveDone();
  }

  private terminalKind(): string | null {
    // The projector exists once the first read ran; a stream closed before that has none.
    const projector = this.projector as RunProjector | undefined;
    return projector?.terminal?.kind ?? null;
  }

  private enqueue(step: () => Promise<void>): void {
    this.chain = this.chain.then(async () => {
      if (this.closed) return;
      try {
        await step();
      } catch (error) {
        this.deps.logger.error(
          'projection.failed',
          harnessLogMeta('projection.failed', {
            ...this.fields(),
            errorType: error instanceof Error ? error.name : typeof error,
          }),
        );
        this.sink.end();
        this.close('projection_failed');
      }
    });
  }

  /**
   * Through the Node's reader: what a stream or a commit here already read is not read again (QA22-F3).
   * `committed`: the seq a tail signal named, which a store read under way may already cover.
   */
  private readRunJournal(
    runId: string,
    after: number,
    committed?: number,
  ): Promise<JournalEvent[]> {
    return this.deps.runJournal.read(
      this.request.run.orgId,
      runId,
      after,
      committed,
    );
  }

  private async readJournal(
    after: number,
    committed?: number,
  ): Promise<JournalEvent[]> {
    return this.decided(
      await this.readRunJournal(this.request.run.runId, after, committed),
    );
  }

  /** A shared view's access to what `events` cite and present is decided before any of them is projected. */
  private async decided(events: JournalEvent[]): Promise<JournalEvent[]> {
    await this.decideCitations(events);
    await this.decideArtifacts(events);
    return events;
  }

  /**
   * A shared view checks its access once, before the run's end is projected:
   * every citation frame (the final `/citations`, the final snapshot,
   * `RUN_FINISHED`) comes at or after it. The check covers what the journal
   * cited and the finished conversation `RUN_FINISHED` carries.
   */
  private async decideCitations(
    events: readonly JournalEvent[],
  ): Promise<void> {
    if (this.request.audience !== 'shared' || this.citations !== null) return;
    const run = this.request.run;
    let ended = false;
    for (const event of events) {
      if (event.runId !== run.runId) continue;
      for (const source of checkedSources(event.data?.['citations'])) {
        this.citedSources.set(sourceKey(source), source);
      }
      if (TERMINAL_EVENTS.has(event.type)) ended = true;
    }
    if (!ended) return;
    const conversation =
      await this.deps.conversations.finishedConversation(run);
    this.finished = { conversation };
    this.citations = await this.deps.sharedCitations.decide(
      {
        authorization: this.request.authorization,
        orgId: run.orgId,
        sessionId: run.sessionId,
        runId: run.runId,
        ...(this.request.requestId !== undefined
          ? { requestId: this.request.requestId }
          : {}),
        surface: 'stream',
      },
      [
        ...this.citedSources.values(),
        ...messageSources(conversation?.['messages']),
      ],
    );
  }

  /**
   * A shared view decides each artifact once, before any frame naming it is
   * projected: the card a tool presented, the markers of the finalized answer
   * and, at the end, those of the finished conversation. One batched check
   * per journal read that brings new artifacts.
   */
  private async decideArtifacts(
    events: readonly JournalEvent[],
  ): Promise<void> {
    if (this.request.audience !== 'shared') return;
    const run = this.request.run;
    const named: string[] = [];
    for (const event of events) {
      if (event.runId !== run.runId) continue;
      if (event.type === ARTIFACT_PRESENTED) {
        const id = cardArtifactId(event.data);
        if (id !== undefined) named.push(id);
      }
      named.push(...markerArtifactIds(event.data?.['answer']));
    }
    named.push(
      ...messageArtifactIds(this.finished?.conversation?.['messages']),
    );
    const fresh = [...new Set(named)].filter(
      (id) => !this.decidedArtifacts.has(id),
    );
    if (fresh.length === 0) return;
    for (const id of fresh) this.decidedArtifacts.add(id);
    const decided = await this.deps.sharedArtifacts.decide(
      {
        authorization: this.request.authorization,
        orgId: run.orgId,
        sessionId: run.sessionId,
        runId: run.runId,
        ...(this.request.requestId !== undefined
          ? { requestId: this.request.requestId }
          : {}),
        surface: 'stream',
      },
      fresh,
    );
    this.artifacts = this.artifacts.with(decided);
  }

  /**
   * A projector rebuilt from the journal (and the LiveBus's retained deltas)
   * as of the frame `cursorId`, and the frames after it. Reconnects (L-1, L-2)
   * and a client falling behind (L-4) both go through here, so what follows an
   * ended message depends only on the id the client has, never on how it
   * got there.
   */
  private async rebuild(
    cursorId: string | null,
    options: ReplayOptions,
  ): Promise<{
    frames: ProjectedFrame[];
    cursor: StreamCursor | null;
    journalEvents: number;
    retainedDeltas: number;
    busCursor: string;
    livebus: 'redis' | 'none' | 'unavailable';
  }> {
    const run = this.request.run;
    const cursor = parseStreamId(cursorId ?? undefined);
    const events = await this.readJournal(0);
    let retained: LiveDelta[] = [];
    let busCursor = '';
    let livebus: 'redis' | 'none' | 'unavailable' =
      this.deps.liveBus === null ? 'none' : 'redis';
    if (this.deps.liveBus !== null) {
      try {
        const read = await this.deps.liveBus.read(run.runId);
        retained = read.deltas;
        busCursor = read.cursor;
      } catch (error) {
        livebus = 'unavailable';
        this.liveBusDown(error);
      }
    }
    const plan = planReplay(run.runId, events, retained, cursor, options);
    this.projector = this.newProjector(plan.resumeAfter);
    this.projector.restoreLost(plan.lostMessages);
    const frames = this.projector.begin();
    if (plan.lost !== null) {
      if (options.forceLost !== true) {
        this.deps.logger.info(
          'projection.buffer_expired',
          harnessLogMeta('projection.buffer_expired', {
            ...this.fields(),
            fence: plan.lost.fence,
            modelAttempt: plan.lost.attempt,
            n: plan.lost.n,
            choice: plan.lost.endSent
              ? 'continue_after_sent_end'
              : 'restart_message_from_journal',
          }),
        );
      }
      frames.push(
        ...this.projector.abandon(
          plan.lost,
          plan.lost.endSent || cursorId === null
            ? 'already_sent'
            : { sentAfter: positionalId(cursorId) },
        ),
      );
    }
    for (const step of plan.steps) {
      frames.push(
        ...('journal' in step
          ? this.projector.project(step.journal)
          : this.projector.delta(step.delta)),
      );
    }
    return {
      frames,
      cursor,
      journalEvents: events.length,
      retainedDeltas: retained.length,
      busCursor,
      livebus,
    };
  }

  private newProjector(resumeAfter: StreamPosition | null): RunProjector {
    const run = this.request.run;
    return new RunProjector(
      {
        runId: run.runId,
        sessionId: run.sessionId,
        conversationId: run.sessionId,
        ...(this.request.title !== undefined
          ? { title: this.request.title }
          : {}),
        goal: run.input?.query ?? '',
        audience: this.request.audience,
        // Only the owner's own client words a failure itself; every other audience keeps the legacy frame.
        ...(this.request.frames !== undefined &&
        this.request.audience === 'owner'
          ? { frames: this.request.frames }
          : {}),
        ...(run.parent !== undefined ? { parentRunId: run.parent.runId } : {}),
        ...(run.status === 'queued' && run.attempts === 0
          ? { queuedAt: new Date(run.createdAt).toISOString() }
          : {}),
        citations: () => this.citations ?? CitationAccess.denyRecords(),
        artifacts: () => this.artifacts,
      },
      resumeAfter,
      {
        fenceDropped: (fence, current) => {
          this.fenceDropped(fence, current);
        },
        deltaGap: (fence, attempt, expected, got) => {
          this.deps.logger.debug(
            'projection.delta_gap',
            harnessLogMeta('projection.delta_gap', {
              ...this.fields(),
              fence,
              modelAttempt: attempt,
              expected,
              got,
            }),
          );
        },
      },
    );
  }

  private async catchUp(): Promise<void> {
    const run = this.request.run;
    const lastEventId = this.request.lastEventId ?? null;
    const rebuilt = await this.rebuild(lastEventId, {});
    const { cursor, frames, busCursor, livebus } = rebuilt;
    if (cursor !== null) {
      this.lastSentId = lastEventId;
      this.stated = cursor.stated;
    }
    // Before the first frame: every id names the cards this client holds.
    if (this.request.audience === 'owner') {
      this.children = new ChildInteractions(
        (childRunId, after) => this.readRunJournal(childRunId, after),
        run.runId,
        cursor?.held,
      );
    }
    this.deps.logger.info(
      'projection.subscribe',
      harnessLogMeta('projection.subscribe', {
        ...this.fields(),
        resume:
          cursor === null
            ? 'fresh'
            : cursor.abandoned === true
              ? 'after_end'
              : cursor.kind === 'j'
                ? 'journal'
                : 'delta',
        seq: cursor?.position.seq ?? null,
        livebus,
        journalEvents: rebuilt.journalEvents,
        retainedDeltas: rebuilt.retainedDeltas,
      }),
    );
    this.send(frames);
    if (await this.finishIfTerminal()) return;
    this.pushCost();
    this.followChildren();
    this.journalSub = this.deps.runJournal.follow(
      run.orgId,
      run.runId,
      () => this.projector.journalSeq,
      (events) => {
        this.enqueue(async () => this.take(await this.decided(events)));
      },
    );
    this.tailSub = this.deps.tail.watch(run.orgId, run.runId, (seq) => {
      this.enqueue(() => this.pull(seq));
    });
    if (this.deps.liveBus !== null && livebus === 'redis') {
      this.busSub = this.deps.liveBus.follow(
        run.runId,
        busCursor,
        (deltas) => {
          this.enqueue(async () => {
            if (deltas.some((delta) => this.projector.startsMessage(delta))) {
              await this.pull();
            }
            this.onDeltas(deltas);
          });
        },
        (error) => {
          this.enqueue(() => Promise.resolve(this.liveBusDown(error)));
        },
      );
    }
    // Anything committed between the first read and the watch.
    await this.pull();
  }

  /** `committed`: the seq a tail signal named; one this stream has projected needs no read. */
  private async pull(committed?: number): Promise<void> {
    if (committed !== undefined && committed <= this.projector.journalSeq) {
      return;
    }
    await this.take(
      await this.readJournal(this.projector.journalSeq, committed),
    );
  }

  /** Projects the events of `events` this stream has not projected yet. */
  private async take(events: readonly JournalEvent[]): Promise<void> {
    const after = this.projector.journalSeq;
    const fresh = events.filter((event) => event.seq > after);
    if (fresh.length > 0) {
      this.send(this.projector.project(fresh));
      this.followChildren();
      this.cost?.tick();
    }
    await this.finishIfTerminal();
  }

  /**
   * C10.05 (WP-100): the owner's client that reads the outcome frames gets the run's spend as the ledger moves, as
   * a state frame (placed after the last positional frame, never replayed by a resume: the next read says it).
   */
  private pushCost(): void {
    const spend = this.deps.spend;
    if (
      spend === null ||
      this.request.audience !== 'owner' ||
      !servesOutcomeFrames(this.request.frames)
    ) {
      return;
    }
    const run = this.request.run;
    this.cost = new CostPush({
      runId: run.runId,
      read: () => spend.read(run),
      schedule: (step) => {
        this.enqueue(step);
      },
      send: (frame) => {
        this.sendState([{ frame, held: this.children?.held() ?? [] }]);
      },
      failed: (error) => {
        this.deps.logger.debug(
          'projection.cost_unread',
          harnessLogMeta('projection.cost_unread', {
            ...this.fields(),
            errorType: error instanceof Error ? error.name : typeof error,
          }),
        );
      },
      ...(this.deps.costPushMs !== undefined
        ? { minMs: this.deps.costPushMs }
        : {}),
    });
    this.cost.tick();
  }

  /** Starts and stops following the children the run has live now (UX3). */
  private followChildren(): void {
    const children = this.children;
    if (children === null) return;
    const run = this.request.run;
    const { added, removed, settle, frames } = children.track(
      this.projector.children(),
    );
    for (const childRunId of removed) {
      this.childSubs.get(childRunId)?.close();
      this.childSubs.delete(childRunId);
    }
    this.sendState(frames);
    const pullChild = (childRunId: string) => (): Promise<void> =>
      children.pull(childRunId).then((found) => {
        this.sendState(found);
      });
    for (const childRunId of added) {
      this.childSubs.set(
        childRunId,
        this.deps.tail.watch(run.orgId, childRunId, () => {
          this.enqueue(pullChild(childRunId));
          // A child's spend is its parent's too (the ledger tree).
          this.cost?.tick();
        }),
      );
    }
    for (const childRunId of [...added, ...settle]) {
      this.enqueue(pullChild(childRunId));
    }
  }

  /**
   * Frames that say a child's card rather than a journal position: each is
   * placed after the last positional frame (`+n`) and names the cards the
   * client holds once it has it, so a resume from it repeats and drops none.
   */
  private sendState(frames: readonly StateFrame[]): void {
    if (this.lastSentId === null) return;
    const position = positionalId(this.lastSentId);
    for (const { frame, held } of frames) {
      if (this.closed) return;
      this.stated += 1;
      this.write({ id: withChildState(position, held, this.stated), frame });
    }
    this.armHeld();
  }

  /**
   * WP115-FU-2: an ask a held grant covers is drawn with Node's answer; one Node has not answered when its grace
   * ends is drawn then, for the person to decide.
   */
  private armHeld(): void {
    if (this.closed || this.heldTimer !== null) return;
    const projector = this.projector as RunProjector | undefined;
    const dues = [projector?.heldUntil(), this.children?.heldUntil()].filter(
      (until): until is number => typeof until === 'number',
    );
    if (dues.length === 0) return;
    const timer = spawnDetached(() =>
      setTimeout(
        () => {
          this.heldTimer = null;
          this.enqueue(() => {
            const now = Date.now();
            this.send(this.projector.releaseHeld(now));
            if (this.children !== null) {
              this.sendState(this.children.releaseHeld(now));
            }
            this.armHeld();
            return Promise.resolve();
          });
        },
        Math.max(0, Math.min(...dues) - Date.now()),
      ),
    );
    timer.unref();
    this.heldTimer = timer;
  }

  private onDeltas(deltas: LiveDelta[]): void {
    // A queued rebuild re-reads everything retained, these included.
    if (this.resyncing) return;
    for (const delta of deltas) {
      if (this.congested) {
        this.resync();
        return;
      }
      this.send(this.projector.delta(delta));
    }
  }

  /**
   * The client is not keeping up (L-4): stop streaming deltas and end the
   * message it has; the journal restarts it whole when the model responds.
   * The worker never waits for any of this.
   */
  private resync(): void {
    if (this.resyncing) return;
    const open = this.projector.openMessage;
    if (open === null) return;
    this.resyncing = true;
    this.deps.logger.warn(
      'projection.overflow_resync',
      harnessLogMeta('projection.overflow_resync', {
        ...this.fields(),
        fence: open.fence,
        modelAttempt: open.attempt,
        n: open.n,
        pendingBytes: this.sink.pending,
        choice: 'restart_message_from_journal',
      }),
    );
    this.enqueue(async () => {
      const { frames } = await this.rebuild(this.lastSentId, {
        forceLost: true,
      });
      this.resyncing = false;
      this.send(frames, true);
      await this.finishIfTerminal();
    });
  }

  private liveBusDown(error: unknown): void {
    dependencyHealth.report('kv');
    this.busSub?.close();
    this.busSub = null;
    this.deps.logger.warn(
      'projection.livebus_unavailable',
      harnessLogMeta('projection.livebus_unavailable', {
        ...this.fields(),
        errorType: error instanceof Error ? error.name : typeof error,
        choice: 'journal_only',
      }),
    );
  }

  private fenceDropped(fence: number, current: number): void {
    if (this.droppedFences.has(fence)) return;
    this.droppedFences.add(fence);
    this.deps.logger.info(
      'projection.fence_dropped',
      harnessLogMeta('projection.fence_dropped', {
        ...this.fields(),
        staleFence: fence,
        currentFence: current,
        reason: 'lower_fence',
      }),
    );
  }

  private send(frames: ProjectedFrame[], force = false): void {
    for (const frame of frames) {
      if (this.closed) return;
      // A queued rebuild regenerates everything after the last frame written.
      if (!force && this.resyncing) continue;
      if (!force && this.congested && frame.id.startsWith('d:')) {
        this.resync();
        continue;
      }
      this.stated = 0;
      this.write(
        this.children === null
          ? frame
          : { ...frame, id: withChildState(frame.id, this.children.held()) },
      );
    }
    this.armHeld();
  }

  private write(frame: ProjectedFrame): void {
    this.frames += 1;
    this.lastSentId = frame.id;
    if (!this.sink.write(formatSseFrame(frame))) this.congested = true;
  }

  /** D-MCP-ASK, D-SLACK-ASK: an audience that cannot show a card ends its stream where the run waits on one. */
  private async handOffIfAwaiting(): Promise<boolean> {
    const audience = this.request.audience;
    if (
      audience === 'owner' ||
      !AUDIENCE_VIEWS[audience].handsOff ||
      !this.projector.awaitingApproval
    ) {
      return false;
    }
    const run = this.request.run;
    const conversation = (await this.deps.conversations.finishedConversation(
      run,
    )) ?? {
      _id: run.sessionId,
      messages: [],
    };
    this.send(
      this.projector.handOff(conversation, {
        requestId: this.request.requestId ?? run.runId,
        timestamp: new Date().toISOString(),
        link: conversationLink(run.sessionId, run.agentKey),
      }),
      true,
    );
    this.deps.logger.info(
      'projection.finished',
      harnessLogMeta('projection.finished', {
        ...this.fields(),
        terminal: 'awaiting_approval',
        code: null,
        frames: this.frames,
      }),
    );
    this.sink.end();
    this.close('awaiting_approval');
    return true;
  }

  private async finishIfTerminal(): Promise<boolean> {
    if (this.resyncing) return false;
    if (await this.handOffIfAwaiting()) return true;
    const terminal = this.projector.terminal;
    if (terminal === null) return false;
    const run = this.request.run;
    // WP100-FU-5: the run's last step is charged before the client hears the run ended.
    await this.cost?.final();
    if (terminal.kind === 'finished') {
      const conversation =
        this.finished !== null
          ? this.finished.conversation
          : await this.deps.conversations.finishedConversation(run);
      if (conversation === null) {
        this.deps.logger.error(
          'projection.conversation_missing',
          harnessLogMeta('projection.conversation_missing', {
            ...this.fields(),
            reason: 'finished_run_has_no_conversation',
          }),
        );
        this.send(
          this.projector.fail(
            'save_error',
            'The conversation could not be loaded.',
          ),
          true,
        );
      } else {
        this.send(
          this.projector.finish(conversation, {
            requestId: this.request.requestId ?? run.runId,
            timestamp: new Date().toISOString(),
            duration: Math.max(0, run.updatedAt - run.createdAt),
          }),
          true,
        );
      }
    }
    this.deps.logger.info(
      'projection.finished',
      harnessLogMeta('projection.finished', {
        ...this.fields(),
        terminal: terminal.kind,
        code: terminal.kind === 'error' ? terminal.code : null,
        frames: this.frames,
      }),
    );
    this.sink.end();
    this.close('terminal');
    return true;
  }
}
