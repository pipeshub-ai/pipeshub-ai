import type { PipeshubCommandFinishedEvent } from '../contracts/generated/ext/pipeshub/events/x-pipeshub-command-finished';
import {
  ActivityPanelDisabledError,
  HarnessNotFoundError,
  JournalPayloadMissingError,
} from '../domain/errors';
import { HarnessRun, RunStatus } from '../domain/execution';
import { Followup } from '../domain/followups';
import { ActivityPanelSetting } from '../ports/activity-panel';
import { SessionExecutionScope } from '../ports/chat-session-repository';
import { JournalStore } from '../ports/journal-store';
import { JournalTail, JournalTailSubscription } from '../ports/journal-tail';
import { LiveBus, LiveBusSubscription } from '../ports/live-bus';
import {
  ActivityCommand,
  ActivityFold,
  ActivityFrame,
  ActivityStepDetail,
  ActivityView,
  applyLive,
  emptyActivity,
  mergeLive,
  withoutHiddenOutput,
  stepDetail,
} from '../projection/activity';
import { SSE_HEARTBEAT } from '../projection/frames';
import type { FrameSink } from '../projection/stream-service';
import { foldTimeline, inlineContent } from '../projection/timeline';
import { RunReader } from './harness-authority';
import {
  harnessLogMeta,
  HarnessLogger,
  runLogFields,
  silentHarnessLogger,
} from './harness-log';
import { JournalService } from './journal-service';
import { RunContentService } from './run-content-service';
import { readRunEvents } from './run-timeline-service';
import { SessionRunService } from './session-run-service';
import { detachedInterval } from '../../../libs/context/orgContext';

const HEARTBEAT_MS = 15_000;
const COMMAND_FINISHED = 'x.pipeshub.command.finished';
const ENDED: ReadonlySet<ActivityView['status']> = new Set([
  'completed',
  'failed',
  'cancelled',
]);

export interface RunActivityDependencies {
  runs: Pick<SessionRunService, 'streamAccess'>;
  /** Re-read on each journal advance: a snapshot's status is the run's now. */
  executions: RunReader;
  journal: Pick<JournalStore<unknown>, 'readRun'>;
  journals: Pick<JournalService, 'resolve'>;
  tail: JournalTail;
  /** The activity bus; absent: snapshots from the journal only (AP-8). */
  bus: LiveBus<ActivityFrame> | null;
  setting: ActivityPanelSetting;
  content: Pick<RunContentService, 'read'>;
  logger?: HarnessLogger;
  heartbeatMs?: number;
}

export interface ActivityStreamHandle {
  close(reason?: string): void;
  /** Settles when the stream ended (the run ended, or the client left). */
  readonly done: Promise<void>;
}

/**
 * The activity panel (UX5): what the run's owner's run did in its sandbox,
 * as a snapshot followed by deltas. Only the run's actor sees it, exactly as
 * only they see tool arguments and output on the chat stream (PRJ-09);
 * anyone else, and every caller while the org's Labs flag is off, is told
 * the run is not found.
 */
export class RunActivityService {
  private readonly logger: HarnessLogger;
  private readonly heartbeatMs: number;

  constructor(private readonly deps: RunActivityDependencies) {
    this.logger = deps.logger ?? silentHarnessLogger;
    this.heartbeatMs = deps.heartbeatMs ?? HEARTBEAT_MS;
  }

  async authorize(
    scope: SessionExecutionScope,
    runId: string,
  ): Promise<HarnessRun> {
    if (!(await this.deps.setting.enabled(scope.orgId))) {
      throw new ActivityPanelDisabledError();
    }
    const { run, access } = await this.deps.runs.streamAccess(scope, runId);
    if (access.audience !== 'owner') throw new HarnessNotFoundError();
    return run;
  }

  /** Streams to `sink` until the run ends or `close`; `run` came from `authorize`. */
  stream(
    run: HarnessRun,
    sink: FrameSink,
    requestId?: string,
  ): ActivityStreamHandle {
    return new ActivityStream(
      this.deps,
      this.logger,
      this.heartbeatMs,
      run,
      sink,
      requestId,
    ).start();
  }

  /**
   * One step's detail (ACTRESULTS): what the chat's step shows of the call, read when the panel opens it, so the
   * snapshot never carries results; not found when the run has no such step.
   */
  async step(
    scope: SessionExecutionScope,
    runId: string,
    toolCallId: string,
  ): Promise<ActivityStepDetail> {
    const run = await this.authorize(scope, runId);
    const events = await readRunEvents(
      this.deps.journal,
      this.deps.journals,
      run,
      0,
    );
    const detail = stepDetail(foldTimeline(events, runId), toolCallId);
    if (detail === null) throw new HarnessNotFoundError();
    return detail;
  }

  /** A finished command's full, redacted output; not found when it has none. */
  async log(
    scope: SessionExecutionScope,
    runId: string,
    commandId: string,
  ): Promise<Buffer> {
    const run = await this.authorize(scope, runId);
    const events = await readRunEvents(
      this.deps.journal,
      this.deps.journals,
      run,
      0,
    );
    const record = events
      .filter(
        (event) => event.type === COMMAND_FINISHED && event.runId === runId,
      )
      .map((event) => event.data as PipeshubCommandFinishedEvent['data'])
      .reverse()
      .find((data) => data.commandId === commandId);
    if (record?.log === undefined) throw new HarnessNotFoundError();
    // C4.10 `ui.terminal` off: the run's command output is never sent, its full log included.
    if (foldTimeline(events, runId).display?.terminal === false) {
      throw new HarnessNotFoundError();
    }
    const inline = inlineContent(record.log);
    if (inline !== null) return Buffer.from(inline, 'utf8');
    try {
      return await this.deps.content.read(run, record.log.ref);
    } catch (error) {
      if (error instanceof JournalPayloadMissingError) {
        throw new HarnessNotFoundError();
      }
      throw error;
    }
  }
}

const sseEvent = (name: string, data: unknown): string =>
  `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

class ActivityStream implements ActivityStreamHandle {
  private readonly fold: ActivityFold;
  private journalSeq = 0;
  private runStatus: RunStatus;
  private followups: Followup[];
  /** What the activity bus said, folded: merged into every snapshot. */
  private live: ActivityView;
  private current: ActivityView;
  private chain: Promise<void> = Promise.resolve();
  private tailSub: JournalTailSubscription | null = null;
  private busSub: LiveBusSubscription | null = null;
  private heartbeat: NodeJS.Timeout | null = null;
  private closed = false;
  /** The client is behind: deltas wait, and one snapshot catches it up on drain. */
  private congested = false;
  private behind = false;
  private resolveDone!: () => void;
  readonly done: Promise<void>;

  constructor(
    private readonly deps: RunActivityDependencies,
    private readonly logger: HarnessLogger,
    private readonly heartbeatMs: number,
    private readonly run: HarnessRun,
    private readonly sink: FrameSink,
    private readonly requestId?: string,
  ) {
    this.runStatus = run.status;
    this.followups = run.followups ?? [];
    this.live = emptyActivity(run.runId);
    this.current = this.live;
    this.fold = new ActivityFold({
      runId: run.runId,
      journalOutput: deps.bus === null,
    });
    this.done = new Promise((resolve) => {
      this.resolveDone = resolve;
    });
  }

  private fields(): Record<string, string | number | null> {
    return { ...runLogFields(this.run), requestId: this.requestId ?? null };
  }

  start(): this {
    this.enqueue(() => this.catchUp());
    this.heartbeat = detachedInterval(() => {
      if (!this.closed) this.sink.write(SSE_HEARTBEAT);
    }, this.heartbeatMs);
    this.sink.onDrain(() => {
      this.congested = false;
      if (this.behind) {
        this.behind = false;
        this.enqueue(() => {
          this.snapshot();
        });
      }
    });
    return this;
  }

  close(reason = 'client_closed'): void {
    if (this.closed) return;
    this.closed = true;
    this.tailSub?.close();
    this.busSub?.close();
    if (this.heartbeat !== null) clearInterval(this.heartbeat);
    this.logger.info(
      'activity.unsubscribe',
      harnessLogMeta('activity.unsubscribe', {
        ...this.fields(),
        reason,
        status: this.current.status,
      }),
    );
    this.resolveDone();
  }

  private enqueue(step: () => Promise<void> | void): void {
    this.chain = this.chain.then(async () => {
      if (this.closed) return;
      try {
        await step();
      } catch (error) {
        this.logger.error(
          'activity.failed',
          harnessLogMeta('activity.failed', {
            ...this.fields(),
            errorType: error instanceof Error ? error.name : typeof error,
          }),
        );
        this.sink.end();
        this.close('activity_failed');
      }
    });
  }

  private async catchUp(): Promise<void> {
    const run = this.run;
    let cursor = '';
    let bus: 'redis' | 'none' | 'unavailable' =
      this.deps.bus === null ? 'none' : 'redis';
    if (this.deps.bus !== null) {
      try {
        const retained = await this.deps.bus.read(run.runId);
        this.live = retained.deltas.reduce(applyLive, this.live);
        cursor = retained.cursor;
      } catch (error) {
        bus = 'unavailable';
        this.busDown(error);
      }
    }
    await this.readJournal();
    this.logger.info(
      'activity.subscribe',
      harnessLogMeta('activity.subscribe', {
        ...this.fields(),
        livebus: bus,
        journalEvents: this.journalSeq,
      }),
    );
    if (this.snapshot()) return;
    this.tailSub = this.deps.tail.watch(run.orgId, run.runId, () => {
      this.enqueue(() => this.pull());
    });
    if (this.deps.bus !== null && bus === 'redis') {
      this.busSub = this.deps.bus.follow(
        run.runId,
        cursor,
        (frames) => {
          this.enqueue(() => this.onFrames(frames));
        },
        (error) => {
          this.enqueue(() => {
            this.busDown(error);
          });
        },
      );
    }
    // Anything committed between the first read and the watch.
    await this.pull();
  }

  private async readJournal(): Promise<boolean> {
    const events = await readRunEvents(
      this.deps.journal,
      this.deps.journals,
      this.run,
      this.journalSeq,
    );
    if (events.length === 0) return false;
    for (const event of events) this.fold.apply(event);
    this.journalSeq = events[events.length - 1]?.seq ?? this.journalSeq;
    const now = await this.deps.executions.get(this.run.orgId, this.run.runId);
    if (now !== null) {
      this.runStatus = now.status;
      this.followups = now.followups ?? [];
    }
    return true;
  }

  private async pull(): Promise<void> {
    if (await this.readJournal()) this.snapshot();
  }

  /** True when the snapshot has the run's end: `end` follows, and the stream closes. */
  private snapshot(): boolean {
    this.current = withoutHiddenOutput(
      mergeLive(this.fold.view(this.runStatus, this.followups), this.live),
    );
    this.write('snapshot', this.current, true);
    if (!ENDED.has(this.current.status)) return false;
    this.write('end', { status: this.current.status }, true);
    this.sink.end();
    this.close('run_ended');
    return true;
  }

  private async onFrames(frames: readonly ActivityFrame[]): Promise<void> {
    // C4.10 `ui.terminal`: a command's output may only be sent once the run's pinned spec is folded, so a frame
    // that overtook the claim's commit waits for the journal; a run whose journal pins no spec shows it.
    if (
      this.fold.display === undefined &&
      frames.some((frame) => frame.kind === 'command_output')
    ) {
      await this.readJournal();
    }
    for (const frame of frames) {
      this.live = applyLive(this.live, frame);
      const before = this.current;
      this.current = applyLive(before, frame);
      if (this.current === before) continue;
      const commandId = frame.data['commandId'] as string;
      if (frame.kind === 'command_output') {
        // C4.10: output is sent only once the pin is folded and shows the terminal; the next snapshot, hidden
        // or not, carries what was held back.
        if (this.fold.display?.terminal !== false)
          this.write('output', outputOf(frame));
        else this.current = before;
        continue;
      }
      const command = this.current.commands.find(
        (c) => c.commandId === commandId,
      );
      if (command !== undefined) this.write('command', command);
    }
  }

  private busDown(error: unknown): void {
    this.busSub?.close();
    this.busSub = null;
    this.logger.warn(
      'activity.livebus_unavailable',
      harnessLogMeta('activity.livebus_unavailable', {
        ...this.fields(),
        errorType: error instanceof Error ? error.name : typeof error,
        choice: 'journal_only',
      }),
    );
  }

  /** A delta to a client that is behind is dropped; the snapshot on drain carries it. */
  private write(name: string, data: unknown, whole = false): void {
    if (this.closed) return;
    if (this.congested && !whole) {
      this.behind = true;
      return;
    }
    if (!this.sink.write(sseEvent(name, data))) this.congested = true;
  }
}

function outputOf(frame: ActivityFrame): Pick<ActivityCommand, 'commandId'> & {
  stream: 'stdout' | 'stderr';
  text?: string;
  dropped?: number;
} {
  const data = frame.data;
  const text = data['text'];
  return {
    commandId: data['commandId'] as string,
    stream: data['stream'] === 'stderr' ? 'stderr' : 'stdout',
    ...(typeof text === 'string'
      ? { text }
      : { dropped: data['dropped'] as number }),
  };
}
