import {
  NO_UNFURL_OPTIONS,
  SLACK_STREAM_MESSAGE_CHAR_LIMIT,
  type SlackBlock,
  type TypedSlackClient,
} from '../helpers';
import { cleanNarration } from './activity-ui';

/** Title of the plan block that heads a streamed answer. */
export const SLACK_PLAN_TITLE = 'Thinking';
const MAX_TASK_DETAILS_CHARS = 600;
const MAX_TASK_SOURCES = 10;
const APPROVAL_OUTPUT = 'Waiting for approval';
/** Tools whose results list web pages, shown as the task's sources. */
const WEB_SEARCH_TOOL = /(?:^|__)web_search$/;
/** Tools that read one page: that page is the source, not the links inside it. */
const WEB_PAGE_TOOL = /(?:^|__)(?:fetch_url|web_scrape)$/;
const URL_PATTERN = /https?:\/\/[^\s"'<>)\]}\\]+/g;
const RESULT_URL_FIELD = /"(?:url|link)"\s*:\s*"(https?:\/\/[^"\\]+)"/;
const FENCE_LINE = /^\s*```/gm;
/** Room kept free in a full message to close a code fence before moving on. */
const FENCE_CLOSE = '\n```\n';
const FENCE_OPEN = '```\n';

export type SlackTaskStatus = 'pending' | 'in_progress' | 'complete' | 'error';

export interface SlackUrlSource {
  type: 'url';
  url: string;
  text: string;
}

export interface SlackTaskChunk {
  type: 'task_update';
  id: string;
  title: string;
  status: SlackTaskStatus;
  details?: string;
  output?: string;
  sources?: SlackUrlSource[];
}

export type SlackStreamChunk =
  | SlackTaskChunk
  | { type: 'plan_update'; title: string }
  | { type: 'markdown_text'; text: string };

function clip(text: string): string {
  const trimmed = text.trim();
  return trimmed.length <= MAX_TASK_DETAILS_CHARS
    ? trimmed
    : `${trimmed.slice(0, MAX_TASK_DETAILS_CHARS).trimEnd()}…`;
}

function slackErrorCode(error: unknown): string | undefined {
  return (error as { data?: { error?: string } } | undefined)?.data?.error;
}

/** The web pages a web tool's result names, for the task's sources. */
export function webResultSources(
  toolName: string,
  content: unknown,
): SlackUrlSource[] {
  if (typeof content !== 'string') return [];
  let urls: string[] = [];
  if (WEB_PAGE_TOOL.test(toolName)) {
    const page = RESULT_URL_FIELD.exec(content)?.[1];
    urls = page ? [page] : [];
  } else if (WEB_SEARCH_TOOL.test(toolName)) {
    urls = [...content.matchAll(URL_PATTERN)].map((m) =>
      m[0].replace(/[.,;:]+$/, ''),
    );
  }
  const sources: SlackUrlSource[] = [];
  const seen = new Set<string>();
  for (const url of urls) {
    if (seen.has(url)) continue;
    let host: string;
    try {
      host = new URL(url).hostname;
    } catch {
      continue;
    }
    seen.add(url);
    sources.push({ type: 'url', url, text: host.replace(/^www\./, '') });
    if (sources.length >= MAX_TASK_SOURCES) break;
  }
  return sources;
}

/**
 * Turns agent activity into tasks for Slack's plan block, keeping each
 * task's latest state.
 */
export class SlackTaskPlan {
  private readonly tasks = new Map<string, SlackTaskChunk>();
  private readonly openIdsByKey = new Map<string, string[]>();
  private pendingDetails: string[] = [];
  private reasoning = '';
  private finishedReasoning = '';
  private seq = 0;

  /** Text the agent wrote before a tool call: it explains the next step. */
  narration(text: string): void {
    const cleaned = cleanNarration(text);
    if (cleaned) this.pendingDetails.push(cleaned);
  }

  reasoningDelta(delta: string): void {
    this.reasoning += delta;
  }

  /** A reasoning segment ended. It is shown with the next step, so a discarded call can still take it back. */
  reasoningDone(): void {
    this.finishedReasoning = [this.finishedReasoning, this.reasoning.trim()]
      .filter(Boolean)
      .join('\n\n');
    this.reasoning = '';
  }

  /** The model call that streamed this reasoning failed and is retried. */
  dropReasoning(): void {
    this.reasoning = '';
    this.finishedReasoning = '';
  }

  /** Finished reasoning as its own step, before the next tool or the answer. */
  flushReasoning(): SlackTaskChunk | null {
    const details = clip(this.finishedReasoning);
    this.finishedReasoning = '';
    if (!details) return null;
    return this.put({
      type: 'task_update',
      id: `reasoning-${++this.seq}`,
      title: 'Reasoning',
      status: 'complete',
      details,
    });
  }

  /** What a running tool's server says about its progress. Null when nothing changed. */
  toolProgress(toolName: string, progress: string): SlackTaskChunk | null {
    const id = this.openIdsByKey.get(`tool:${toolName}`)?.[0];
    const task = id ? this.tasks.get(id) : undefined;
    const output = progress.trim();
    if (!task || !output || task.output === output) return null;
    return this.put({ ...task, output });
  }

  toolStarted(
    toolName: string,
    title: string,
    toolCallId?: string,
  ): SlackTaskChunk {
    return this.open(`tool:${toolName}`, toolCallId, title);
  }

  toolFinished(
    toolName: string,
    title: string,
    status: string | undefined,
    toolCallId?: string,
    content?: unknown,
  ): SlackTaskChunk {
    if (status === 'awaiting_approval') {
      // The turn ends here until someone answers: the step waits, it didn't fail.
      return this.close(`tool:${toolName}`, toolCallId, {
        title,
        status: 'pending',
        output: APPROVAL_OUTPUT,
      });
    }
    const sources = webResultSources(toolName, content);
    return this.close(`tool:${toolName}`, toolCallId, {
      title,
      status:
        status === 'failed' || status === 'blocked' ? 'error' : 'complete',
      ...(sources.length > 0 ? { sources } : {}),
    });
  }

  subAgent(
    role: string,
    phase: 'started' | 'completed' | 'failed',
  ): SlackTaskChunk {
    if (phase === 'started') return this.open(`agent:${role}`, undefined, role);
    return this.close(`agent:${role}`, undefined, {
      title: role,
      status: phase === 'failed' ? 'error' : 'complete',
    });
  }

  /** Running tasks among `ids` when the reply stops: Slack would leave them spinning. */
  failRunning(ids: ReadonlySet<string>): SlackTaskChunk[] {
    const failed: SlackTaskChunk[] = [];
    for (const task of this.tasks.values()) {
      if (task.status === 'in_progress' && ids.has(task.id)) {
        failed.push(this.put({ ...task, status: 'error' }));
      }
    }
    return failed;
  }

  /** The plan as it stands, to open a message with. */
  snapshot(): SlackStreamChunk[] {
    return [
      { type: 'plan_update', title: SLACK_PLAN_TITLE },
      ...this.tasks.values(),
    ];
  }

  private open(
    key: string,
    givenId: string | undefined,
    title: string,
  ): SlackTaskChunk {
    const id = givenId ?? `${key}-${++this.seq}`;
    this.openIdsByKey.set(key, [...(this.openIdsByKey.get(key) ?? []), id]);
    const details = clip(this.pendingDetails.join('\n\n'));
    this.pendingDetails = [];
    return this.put({
      type: 'task_update',
      id,
      title,
      status: 'in_progress',
      ...(details ? { details } : {}),
    });
  }

  private close(
    key: string,
    givenId: string | undefined,
    update: Pick<SlackTaskChunk, 'title' | 'status' | 'output' | 'sources'>,
  ): SlackTaskChunk {
    const open = this.openIdsByKey.get(key) ?? [];
    const id = givenId ?? open[0] ?? `${key}-${++this.seq}`;
    this.openIdsByKey.set(
      key,
      open.filter((openId) => openId !== id),
    );
    const previous = this.tasks.get(id);
    return this.put({ ...previous, ...update, type: 'task_update', id });
  }

  private put(task: SlackTaskChunk): SlackTaskChunk {
    this.tasks.set(task.id, task);
    return task;
  }
}

/**
 * One streamed reply: a plan block once there is a step to show, the answer
 * as markdown, then any blocks (sources) below it. The first message opens on
 * the first step or answer text, so a run with neither shows no plan. Every
 * call uses `chunks`: Slack rejects a stream that mixes them with top-level
 * `markdown_text`.
 */
export class SlackPlanStream {
  ts: string | null = null;
  /** Slack refused the first message: the caller posts the answer instead. */
  unavailable = false;
  private stopped = false;
  private chars = 0;
  private text = '';
  private everOpened = false;
  private earlier: string[] = [];
  private taskIds = new Set<string>();
  /** Where the current model call's answer text began: message index and the text before it. */
  private segment = { message: 0, kept: '' };

  constructor(
    private readonly client: TypedSlackClient,
    private readonly channel: string,
    private readonly threadTs: string,
    private readonly recipient: Record<string, unknown>,
    readonly plan: SlackTaskPlan,
  ) {}

  async tasks(chunks: SlackTaskChunk[]): Promise<void> {
    if (this.unavailable || this.stopped || chunks.length === 0) return;
    if (!this.ts) {
      // The first message shows the whole plan; a later one picks up from here.
      await this.openWith(this.everOpened ? chunks : this.plan.snapshot());
      return;
    }
    try {
      await this.append(chunks);
      this.track(chunks);
    } catch (error) {
      if (slackErrorCode(error) !== 'message_not_in_streaming_state') {
        console.error('Error updating Slack plan:', error);
        return;
      }
      // A stream lasts ten minutes; the plan goes on in a new message.
      this.retire();
      await this.openWith(chunks);
    }
  }

  /** Appends answer markdown, moving to a new message when one is full. */
  async answer(markdown: string): Promise<void> {
    let rest = markdown;
    while (rest.length > 0 && !this.stopped && !this.unavailable) {
      const room =
        SLACK_STREAM_MESSAGE_CHAR_LIMIT - FENCE_CLOSE.length - this.chars;
      if (this.ts && room <= 0) {
        await this.rollover();
        continue;
      }
      let piece = rest;
      if (rest.length > room) {
        const newline = rest.slice(0, room).lastIndexOf('\n');
        piece = rest.slice(0, newline > 0 ? newline + 1 : room);
      }
      const chunk: SlackStreamChunk = { type: 'markdown_text', text: piece };
      if (!this.ts) {
        if (!(await this.openWith([chunk]))) return;
      } else {
        try {
          await this.append([chunk]);
        } catch (error) {
          if (slackErrorCode(error) !== 'message_not_in_streaming_state') {
            throw error;
          }
          this.retire();
          continue;
        }
      }
      this.chars += piece.length;
      this.text += piece;
      rest = rest.slice(piece.length);
    }
  }

  /**
   * Finishes the reply. False when the text or blocks could not go on the
   * stream (none open, Slack refused, or it had expired): the caller posts them.
   */
  async stop(
    options: { markdown?: string; blocks?: SlackBlock[] } = {},
  ): Promise<boolean> {
    const nothingToDeliver = !options.markdown && !options.blocks?.length;
    if (!this.ts || this.stopped || this.unavailable) return nothingToDeliver;
    const chunks: SlackStreamChunk[] = [...this.plan.failRunning(this.taskIds)];
    if (options.markdown) {
      chunks.push({ type: 'markdown_text', text: options.markdown });
    }
    try {
      await this.client.apiCall('chat.stopStream', {
        channel: this.channel,
        ts: this.ts,
        ...(chunks.length > 0 ? { chunks } : {}),
        ...(options.blocks && options.blocks.length > 0
          ? { blocks: options.blocks }
          : {}),
      });
      this.stopped = true;
      return true;
    } catch (error) {
      this.stopped = true;
      if (slackErrorCode(error) !== 'message_not_in_streaming_state') {
        console.error('Error stopping Slack stream:', error);
        return false;
      }
      return nothingToDeliver;
    }
  }

  /** The next answer text belongs to a new model call; only its text can be dropped. */
  markSegment(): void {
    this.segment = {
      message: this.earlier.length,
      kept: this.ts ? this.text : '',
    };
  }

  /**
   * The backend dropped the current model call and will retry it: take its
   * text back. Its messages are deleted and the reply reopens with the plan's
   * tasks and any answer text from before that call, so only the retry follows.
   */
  async dropSegment(): Promise<void> {
    const all = [...this.earlier, ...(this.ts ? [this.ts] : [])];
    const affected = all.slice(this.segment.message);
    const { kept } = this.segment;
    const shown =
      affected.length > 1 ||
      (affected.length === 1 && (this.ts === null || this.text !== kept));
    if (!shown || this.unavailable) return;
    if (this.ts && !this.stopped) {
      try {
        await this.client.apiCall('chat.stopStream', {
          channel: this.channel,
          ts: this.ts,
        });
      } catch (error) {
        if (slackErrorCode(error) !== 'message_not_in_streaming_state') {
          console.error('Error stopping Slack stream before delete:', error);
        }
      }
    }
    for (const ts of affected) {
      try {
        await this.client.apiCall('chat.delete', { channel: this.channel, ts });
      } catch (error) {
        if (slackErrorCode(error) !== 'message_not_found') {
          console.error('Error deleting Slack stream message:', error);
        }
      }
    }
    const tasks =
      this.segment.message === 0
        ? this.plan.snapshot()
        : this.plan
            .snapshot()
            .filter(
              (chunk) =>
                chunk.type !== 'task_update' || this.taskIds.has(chunk.id),
            );
    this.earlier = this.earlier.slice(0, this.segment.message);
    this.ts = null;
    this.stopped = false;
    this.text = '';
    const opening: SlackStreamChunk[] = kept
      ? [...tasks, { type: 'markdown_text', text: kept }]
      : tasks;
    // With no step and no earlier text there is nothing to show yet: the retry opens the reply.
    const hasTasks = tasks.some((chunk) => chunk.type === 'task_update');
    if ((hasTasks || kept) && (await this.openWith(opening))) {
      this.chars = kept.length;
      this.text = kept;
    }
    this.markSegment();
  }

  /** Deletes every message of this reply (e.g. the run asks a question instead). */
  async discard(): Promise<void> {
    const all = [...this.earlier, ...(this.ts ? [this.ts] : [])];
    if (this.ts && !this.stopped) {
      try {
        await this.client.apiCall('chat.stopStream', {
          channel: this.channel,
          ts: this.ts,
        });
      } catch (error) {
        if (slackErrorCode(error) !== 'message_not_in_streaming_state') {
          console.error('Error stopping Slack stream before delete:', error);
        }
      }
    }
    for (const ts of all) {
      try {
        await this.client.apiCall('chat.delete', { channel: this.channel, ts });
      } catch (error) {
        if (slackErrorCode(error) !== 'message_not_found') {
          console.error('Error deleting Slack stream message:', error);
        }
      }
    }
    this.ts = null;
    this.stopped = true;
    this.earlier = [];
  }

  /** Opens a message in plan mode. False (and unavailable) when Slack refuses the first one. */
  private async openWith(chunks: SlackStreamChunk[]): Promise<boolean> {
    const reopenFence = this.fenceOpen();
    const opening =
      reopenFence && chunks[0]?.type === 'markdown_text'
        ? [{ type: 'markdown_text' as const, text: FENCE_OPEN }, ...chunks]
        : chunks;
    try {
      const result = await this.client.apiCall('chat.startStream', {
        channel: this.channel,
        thread_ts: this.threadTs,
        chunks: opening,
        // Every message, so a step that arrives after a rollover joins a plan, not a stray card.
        task_display_mode: 'plan',
        ...NO_UNFURL_OPTIONS,
        ...this.recipient,
      });
      if (typeof result.ts !== 'string' || !result.ts) {
        throw new Error('Failed to start Slack stream');
      }
      this.ts = result.ts;
    } catch (error) {
      if (this.everOpened) throw error;
      console.error('Error starting Slack stream:', error);
      this.unavailable = true;
      return false;
    }
    this.everOpened = true;
    this.chars = 0;
    this.text = '';
    if (reopenFence && opening !== chunks) {
      this.chars = FENCE_OPEN.length;
      this.text = FENCE_OPEN;
    }
    this.taskIds = new Set();
    this.track(chunks);
    return true;
  }

  private track(chunks: SlackStreamChunk[]): void {
    for (const chunk of chunks) {
      if (chunk.type === 'task_update') this.taskIds.add(chunk.id);
    }
  }

  private fenceOpen(): boolean {
    return (this.text.match(FENCE_LINE)?.length ?? 0) % 2 === 1;
  }

  private async append(chunks: SlackStreamChunk[]): Promise<void> {
    await this.client.apiCall('chat.appendStream', {
      channel: this.channel,
      ts: this.ts,
      chunks,
    });
  }

  /** The current message expired: keep it, and continue in a new one. */
  private retire(): void {
    if (this.ts) this.earlier.push(this.ts);
    this.ts = null;
  }

  private async rollover(): Promise<void> {
    if (this.ts) {
      // A code fence left open would swallow the rest of this message's text.
      const close: SlackStreamChunk[] = this.fenceOpen()
        ? [{ type: 'markdown_text', text: FENCE_CLOSE }]
        : [];
      try {
        await this.client.apiCall('chat.stopStream', {
          channel: this.channel,
          ts: this.ts,
          ...(close.length > 0 ? { chunks: close } : {}),
        });
      } catch (error) {
        if (slackErrorCode(error) !== 'message_not_in_streaming_state') {
          throw error;
        }
      }
    }
    this.retire();
  }
}
