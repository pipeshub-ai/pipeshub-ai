import type { SSEEvent } from '@/lib/api';
import type { MessagePart, SSEEngineProgressEvent } from '@/chat/types';
import { engineSteps, stepFacts, type EngineStep } from '@/chat/engine-steps';
import { answerBlocks } from './answer-blocks';
import { jobStatusOf, type JobStatus } from './job-status';
import { askStamp, askWaiting } from './approval-words';
import { commandTitle } from './command-titles';
import { looksLikeData, plainDetail, toolIcon, toolTitle } from './step-titles';
import type { AnswerBlock, RunSegment, TrailEntry, TrailEntryState } from './view-models';
import type { Words } from './words';

/**
 * A durable run as the design's trail: frames (live, from `followDurableRun`) or timeline parts (a reload, from
 * `GET …/runs/:runId/timeline`) into `RunSegment[]`, each segment the prose the agent said and then the work it
 * did, and one of the six status words.
 *
 * The frames are the legacy AG-UI vocabulary the stream client already consumes (`RUN_STARTED`, `TEXT_MESSAGE_*`,
 * `REASONING_*`, `TOOL_CALL_*`, `STEP_*`, `STATE_SNAPSHOT`, `CUSTOM approval_requested | approval_resolved |
 * ask_user_question | interaction_waiting | engine_progress`, `RUN_FINISHED`, `RUN_ERROR`). A child run's frames
 * (`parentRunId`) are its job's, not this trail's: the parent shows the hand-off, and `engine-worker.ts` shows the job.
 *
 * Every title is plain words (step-titles.ts). Unknown frames are ignored, so a newer server cannot break the trail.
 */

export interface RunTrail {
  status: JobStatus;
  segments: RunSegment[];
  /** Every trail entry, in order: what a compact receipt counts. */
  entries: TrailEntry[];
}

type Frame = Record<string, unknown> & { type: string };

/** Accepts what the stream client hands over (an `SSEEvent`), a bare frame, or a recorded `{ id, frame }`. */
export function frameOf(input: unknown): Frame | null {
  if (!input || typeof input !== 'object') return null;
  const o = input as Record<string, unknown>;
  if (o.frame && typeof o.frame === 'object') return frameOf(o.frame);
  if (typeof o.type === 'string') return o as Frame;
  const event = input as SSEEvent;
  if (event.data && typeof event.data === 'object') {
    const data = event.data as Record<string, unknown>;
    if (typeof data.type === 'string') return data as Frame;
    if (typeof event.event === 'string') return { ...data, type: event.event };
  }
  return null;
}

const str = (v: unknown): string | undefined => (typeof v === 'string' ? v : undefined);

/**
 * Prose (narration, a job's report) into answer blocks through the answer's own renderer (`answerBlocks`): one
 * reader for every piece of answer text, so a heading, a list or a cited link never reaches the screen as markdown.
 */
export function proseBlocks(text: string): AnswerBlock[] {
  return answerBlocks(text);
}

/**
 * The agent's reasoning is one quiet row, "Thought it through", with what it thought folded under it. Its words are
 * never a step title: a sentence of reasoning in the rail reads as a status line or as a step that happened.
 */
function reasoningEntry(id: string, text: string, t: Words, state: TrailEntryState): TrailEntry {
  const clean = text.replace(/\s+/g, ' ').trim();
  const detail = clean && !looksLikeData(clean) ? (clean.length > 600 ? `${clean.slice(0, 597)}...` : clean) : undefined;
  return {
    id,
    kind: 'reasoning',
    event: 'ReasoningMessageContent',
    title: t('ui.trail.reasoning'),
    ...(detail ? { detail } : {}),
    icon: 'neurology',
    ms: 0,
    state,
  };
}

function engineEntry(step: EngineStep, ended: boolean, t: Words): TrailEntry {
  switch (step.kind) {
    case 'command':
      return {
        id: `engine-${step.callId}`,
        kind: 'tool',
        event: 'ToolCallStart',
        title: commandTitle({ command: step.command, status: step.status, exitCode: step.exitCode, output: step.output }, t).title,
        detail: step.command,
        icon: 'terminal',
        ms: 0,
        depth: 1,
        state: step.status === 'running' ? 'running' : step.status === 'failed' ? 'failed' : 'done',
      };
    case 'file':
      return {
        id: `engine-${step.callId}`,
        kind: 'tool-result',
        event: 'ToolCallResult',
        title: t(`ui.trail.${step.file.change}File`, { path: step.file.path }),
        icon: 'draft',
        ms: 0,
        depth: 1,
        state: 'done',
      };
    case 'helper':
      return {
        id: `engine-${step.callId}`,
        kind: 'step',
        event: 'StepStarted',
        title: step.task ? t('ui.trail.helperTask', { task: step.task }) : t('ui.trail.helper'),
        icon: 'group_work',
        ms: 0,
        depth: 1,
        state: step.status === 'running' && !ended ? 'running' : step.status === 'failed' ? 'failed' : 'done',
      };
    default: {
      const { title, detail } = toolTitle({ toolName: step.toolName, displayName: step.displayName, argsSummary: step.argsSummary, step: step.step }, t);
      return {
        id: `engine-${step.callId}`,
        kind: 'tool',
        event: 'ToolCallStart',
        title,
        ...(detail ? { detail } : {}),
        icon: toolIcon({ toolName: step.toolName, step: step.step }),
        ms: 0,
        depth: 1,
        state: step.status === 'running' ? 'running' : step.status === 'failed' ? 'failed' : 'done',
      };
    }
  }
}

function decisionAnswer(decision: string | undefined, t: Words): string | undefined {
  switch (decision) {
    case 'approved':
    case 'allowed':
      return t('ui.trail.approved');
    case 'denied':
    case 'declined':
      return t('ui.trail.declined');
    case 'expired':
      return t('ui.trail.expired');
    case 'cancelled':
    case 'closed':
      return t('ui.trail.closed');
    default:
      return undefined;
  }
}

/**
 * How a run that sent its last frame ended, from its final snapshot's status: a run can finish by stopping, by
 * failing, or by waiting on your answer (`waiting_input`: it asked a question and ended its turn).
 */
export function finishedAs(snapshotStatus: string | null): JobStatus {
  switch (snapshotStatus) {
    case 'stopped':
    case 'cancelled':
      return 'stopped';
    case 'failed':
    case 'error':
      return 'couldntFinish';
    case 'waiting_input':
    case 'waiting_approval':
      return 'needsYou';
    default:
      return 'done';
  }
}

/** Builds the trail as frames arrive. `view()` is cheap to call after every frame. */
export class TrailReducer {
  private rootRunId: string | null = null;
  private segments: Array<{ id: string; prose: string; entries: TrailEntry[] }> = [];
  private messageText = new Map<string, string>();
  private reasoningText = new Map<string, string>();
  private tools = new Map<string, TrailEntry>();
  private toolNames = new Map<string, string>();
  private asks = new Map<string, TrailEntry>();
  private askCalls = new Map<string, { toolName: string; displayName?: string }>();
  private engineFrames: SSEEngineProgressEvent[] = [];
  private engineEntryIds = new Set<string>();
  private snapshotStatus: string | null = null;
  private waiting = 0;
  private outcome: JobStatus | null = null;

  constructor(private readonly t: Words) {}

  private current(): { id: string; prose: string; entries: TrailEntry[] } {
    let seg = this.segments[this.segments.length - 1];
    if (!seg) {
      seg = { id: `segment-${this.segments.length + 1}`, prose: '', entries: [] };
      this.segments.push(seg);
    }
    return seg;
  }

  /** Prose after work starts the next segment: say, do, say. */
  private proseSegment(): { id: string; prose: string; entries: TrailEntry[] } {
    const seg = this.current();
    if (seg.entries.length === 0) return seg;
    const next = { id: `segment-${this.segments.length + 1}`, prose: '', entries: [] as TrailEntry[] };
    this.segments.push(next);
    return next;
  }

  private add(entry: TrailEntry): void {
    this.current().entries.push(entry);
  }

  apply(input: unknown): void {
    const f = frameOf(input);
    if (!f) return;
    const runId = str(f.runId);
    const childFrame = Boolean(str(f.parentRunId)) || (runId !== undefined && this.rootRunId !== null && runId !== this.rootRunId && f.type !== 'CUSTOM');
    switch (f.type) {
      case 'RUN_STARTED':
        if (!str(f.parentRunId) && !this.rootRunId) this.rootRunId = runId ?? null;
        return;
      case 'TEXT_MESSAGE_START': {
        if (childFrame) return;
        const id = str(f.messageId) ?? 'message';
        if (!this.messageText.has(id)) this.messageText.set(id, '');
        this.proseSegment();
        return;
      }
      case 'TEXT_MESSAGE_CONTENT': {
        if (childFrame) return;
        const delta = str(f.delta) ?? '';
        const seg = this.current().entries.length > 0 ? this.proseSegment() : this.current();
        seg.prose += delta;
        return;
      }
      case 'TEXT_MESSAGE_END': {
        if (childFrame) return;
        const seg = this.current();
        if (seg.prose && !seg.prose.endsWith('\n\n')) seg.prose += '\n\n';
        return;
      }
      case 'REASONING_MESSAGE_START': {
        if (childFrame) return;
        const id = `reasoning-${str(f.messageId) ?? this.reasoningText.size}`;
        this.reasoningText.set(id, '');
        this.add(reasoningEntry(id, '', this.t, 'running'));
        return;
      }
      case 'REASONING_MESSAGE_CONTENT': {
        if (childFrame) return;
        const id = `reasoning-${str(f.messageId) ?? this.reasoningText.size - 1}`;
        const text = (this.reasoningText.get(id) ?? '') + (str(f.delta) ?? '');
        this.reasoningText.set(id, text);
        this.replace(id, reasoningEntry(id, text, this.t, 'running'));
        return;
      }
      case 'REASONING_MESSAGE_END': {
        if (childFrame) return;
        const id = `reasoning-${str(f.messageId) ?? ''}`;
        this.replace(id, reasoningEntry(id, this.reasoningText.get(id) ?? '', this.t, 'done'));
        return;
      }
      case 'TOOL_CALL_START': {
        if (childFrame) return;
        const id = str(f.toolCallId) ?? `tool-${this.tools.size}`;
        const toolName = str(f.toolCallName) ?? str(f.toolName) ?? 'tool';
        const { title, detail } = toolTitle({ toolName, displayName: str(f.displayName) }, this.t);
        const entry: TrailEntry = { id, kind: 'tool', event: 'ToolCallStart', title, ...(detail ? { detail } : {}), icon: toolIcon({ toolName }), ms: 0, state: 'running' };
        this.tools.set(id, entry);
        this.toolNames.set(id, toolName);
        this.add(entry);
        return;
      }
      case 'TOOL_CALL_ARGS': {
        if (childFrame) return;
        const id = str(f.toolCallId) ?? '';
        const entry = this.tools.get(id);
        const summary = plainDetail(str(f.argsSummary));
        if (entry && summary && !entry.detail) entry.detail = summary;
        return;
      }
      case 'TOOL_CALL_RESULT': {
        if (childFrame) return;
        const id = str(f.toolCallId) ?? '';
        const entry = this.tools.get(id);
        if (!entry) return;
        const status = str(f.status);
        entry.state = status === 'failed' || status === 'error' ? 'failed' : 'done';
        const facts = stepFacts(f.step);
        if (facts) {
          const named = toolTitle({ toolName: this.toolNames.get(id) ?? 'tool', step: facts }, this.t);
          entry.title = named.title;
          entry.icon = toolIcon({ toolName: 'tool', step: facts });
        }
        const summary = plainDetail(str(f.resultSummary));
        if (summary && summary !== 'Completed successfully') entry.detail = entry.detail ? `${entry.detail} · ${summary}` : summary;
        return;
      }
      case 'STEP_STARTED': {
        if (childFrame) return;
        const name = str(f.stepName) ?? '';
        // A sub-agent step under a code_task call is the hand-off the tool row already shows.
        if (name.startsWith('sub_agent:') && [...this.tools.entries()].some(([id, e]) => e.state === 'running' && this.toolNames.get(id) === 'code_task')) return;
        if (name.startsWith('sub_agent:')) this.add({ id: `step-${name}`, kind: 'step', event: 'StepStarted', title: this.t('ui.trail.helper'), icon: 'group_work', ms: 0, state: 'running' });
        return;
      }
      case 'STEP_FINISHED': {
        const entry = this.find(`step-${str(f.stepName) ?? ''}`);
        if (entry) entry.state = 'done';
        return;
      }
      case 'STATE_SNAPSHOT': {
        if (childFrame) return;
        const snapshot = (f.snapshot ?? {}) as Record<string, unknown>;
        const s = str(snapshot.status);
        if (s) this.snapshotStatus = s;
        return;
      }
      case 'CUSTOM':
        this.custom(f);
        return;
      case 'RUN_FINISHED': {
        if (str(f.parentRunId)) return;
        this.outcome = finishedAs(this.snapshotStatus);
        this.closeOpen(this.outcome === 'done' || this.outcome === 'needsYou' ? 'done' : 'failed');
        return;
      }
      case 'RUN_ERROR':
        if (str(f.parentRunId)) return;
        this.outcome = str(f.code) === 'abort' || str(f.code) === 'cancelled' ? 'stopped' : 'couldntFinish';
        this.closeOpen('failed');
        return;
      default:
        return;
    }
  }

  private custom(f: Frame): void {
    const name = str(f.name);
    const value = (f.value ?? {}) as Record<string, unknown>;
    switch (name) {
      case 'approval_requested': {
        const id = `ask-${str(value.interactionId) ?? str(value.toolCallId) ?? this.asks.size}`;
        const call = { toolName: str(value.toolName) ?? 'tool', displayName: str(value.displayName) };
        this.askCalls.set(id, call);
        const entry: TrailEntry = {
          id,
          kind: 'decision',
          event: 'UserDecision',
          title: askWaiting(this.t, call),
          ...(plainDetail(str(value.argsSummary)) ? { detail: plainDetail(str(value.argsSummary)) } : {}),
          icon: 'person',
          ms: 0,
          state: 'waiting',
        };
        this.asks.set(id, entry);
        this.add(entry);
        return;
      }
      case 'approval_resolved': {
        const entry = this.asks.get(`ask-${str(value.interactionId) ?? ''}`);
        if (!entry) return;
        entry.answer = decisionAnswer(str(value.decision), this.t);
        const call = this.askCalls.get(`ask-${str(value.interactionId) ?? ''}`);
        if (call) entry.title = askStamp(this.t, str(value.decision) ?? 'closed', call);
        entry.icon = 'how_to_reg';
        entry.state = 'done';
        return;
      }
      case 'ask_user_question': {
        // One row for the set: the card asks the questions one at a time, the trail only says it is waiting.
        const data = (value.toolData ?? value) as Record<string, unknown>;
        const questions = (Array.isArray(data.questions) ? (data.questions as Array<Record<string, unknown>>) : []).map((q) => str(q.question)).filter((q): q is string => Boolean(q));
        if (!questions.length) return;
        const id = `question-${this.asks.size}`;
        const title = questions.length === 1 ? questions[0] : this.t('ui.trail.questions.waiting', { count: questions.length });
        const entry: TrailEntry = { id, kind: 'decision', event: 'UserDecision', title, icon: 'person', ms: 0, state: 'waiting' };
        this.asks.set(id, entry);
        this.add(entry);
        return;
      }
      case 'interaction_waiting':
        this.waiting = typeof value.count === 'number' ? value.count : 0;
        return;
      case 'engine_progress': {
        const frame = value as unknown as SSEEngineProgressEvent;
        if (str(f.runId) && this.rootRunId && str(f.runId) !== this.rootRunId) return;
        if (typeof frame.kind !== 'string') return;
        this.engineFrames.push(frame);
        this.syncEngine(false);
        return;
      }
      default:
        return;
    }
  }

  /** An engine root run's own steps, rebuilt from its frames each time (engine-steps.ts owns their meaning). */
  private syncEngine(ended: boolean): void {
    for (const step of engineSteps(this.engineFrames, ended)) {
      const entry = engineEntry(step, ended, this.t);
      if (this.engineEntryIds.has(entry.id)) this.replace(entry.id, entry);
      else {
        this.engineEntryIds.add(entry.id);
        this.add(entry);
      }
    }
  }

  private find(id: string): TrailEntry | undefined {
    for (const seg of this.segments) {
      const hit = seg.entries.find((e) => e.id === id);
      if (hit) return hit;
    }
    return undefined;
  }

  private replace(id: string, next: TrailEntry): void {
    for (const seg of this.segments) {
      const i = seg.entries.findIndex((e) => e.id === id);
      if (i >= 0) {
        seg.entries[i] = next;
        return;
      }
    }
  }

  private closeOpen(state: TrailEntryState): void {
    if (this.engineFrames.length) this.syncEngine(true);
    for (const seg of this.segments) {
      for (const e of seg.entries) {
        if (e.state === 'running') e.state = state;
        // An ask nobody answered before the run ended did not go ahead: never shown as approved.
        if (e.state === 'waiting' && e.kind === 'decision' && this.outcome !== 'needsYou') e.state = 'done';
      }
    }
  }

  view(): RunTrail {
    const openAsk = this.waiting > 0 || [...this.asks.values()].some((e) => e.state === 'waiting');
    // Until the root's own last frame, the run is going: a snapshot's "completed" can precede the final answer.
    const status = this.outcome ?? jobStatusOf(this.snapshotStatus === 'queued' ? 'queued' : 'running', { openAsk });
    return { status, ...shape(this.segments) };
  }
}

function shape(raw: ReadonlyArray<{ id: string; prose: string; entries: TrailEntry[] }>): { segments: RunSegment[]; entries: TrailEntry[] } {
  const segments: RunSegment[] = [];
  const entries: TrailEntry[] = [];
  raw.forEach((seg, i) => {
    const intro = proseBlocks(seg.prose);
    if (intro.length === 0 && seg.entries.length === 0) return;
    const trail = seg.entries.map((e) => ({ ...e }));
    entries.push(...trail);
    segments.push({
      id: seg.id,
      ...(intro.length ? { intro } : {}),
      ...(trail.length ? { trail } : {}),
      ...(i < raw.length - 1 ? { step: true } : {}),
    });
  });
  return { segments, entries };
}

/** A whole recorded or live frame sequence at once. */
export function trailFromFrames(frames: Iterable<unknown>, t: Words): RunTrail {
  const reducer = new TrailReducer(t);
  for (const frame of frames) reducer.apply(frame);
  return reducer.view();
}

/**
 * A finished or reloaded run's saved parts (the timeline's `parts`, or a saved answer's) as the same trail. Parts
 * are what the journal projection keeps, so this agrees with `trailFromFrames` on the same run.
 */
export function trailFromParts(parts: ReadonlyArray<MessagePart>, t: Words, options: { status?: string | null } = {}): RunTrail {
  const raw: Array<{ id: string; prose: string; entries: TrailEntry[] }> = [];
  const current = () => {
    if (!raw.length) raw.push({ id: 'segment-1', prose: '', entries: [] });
    return raw[raw.length - 1];
  };
  let openAsk = false;
  for (const part of parts) {
    switch (part.type) {
      case 'text': {
        const text = part.content ?? '';
        if (!text.trim()) break;
        let seg = current();
        if (seg.entries.length > 0) {
          seg = { id: `segment-${raw.length + 1}`, prose: '', entries: [] };
          raw.push(seg);
        }
        seg.prose += `${text}\n\n`;
        break;
      }
      case 'reasoning':
        current().entries.push(reasoningEntry(`reasoning-${current().entries.length}`, part.content ?? '', t, 'done'));
        break;
      case 'tool_call': {
        const toolName = part.toolName ?? 'tool';
        const { title, detail } = toolTitle({ toolName, displayName: part.displayName, argsSummary: part.argsSummary, step: part.step }, t);
        const state: TrailEntryState =
          part.status === 'failed' || part.status === 'blocked' ? 'failed' : part.status === 'running' ? 'running' : part.status === 'awaiting_approval' ? 'waiting' : 'done';
        if (state === 'waiting') openAsk = true;
        current().entries.push({ id: part.toolCallId ?? `tool-${current().entries.length}`, kind: 'tool', event: 'ToolCallStart', title, ...(detail ? { detail } : {}), icon: toolIcon({ toolName, step: part.step }), ms: 0, state });
        break;
      }
      case 'approval': {
        const call = { toolName: part.toolName ?? 'tool', displayName: part.displayName };
        const pending = part.status === 'pending';
        if (pending) openAsk = true;
        current().entries.push({
          id: `ask-${part.interactionId ?? current().entries.length}`,
          kind: 'decision',
          event: 'UserDecision',
          title: pending ? askWaiting(t, call) : askStamp(t, part.status === 'approved' && part.scope === 'conversation' ? 'conversation' : (part.status ?? 'closed'), call),
          ...(pending ? {} : { answer: decisionAnswer(part.status, t) }),
          icon: pending ? 'person' : 'how_to_reg',
          ms: 0,
          state: pending ? 'waiting' : 'done',
        });
        break;
      }
      case 'engine_task': {
        const frames = ((part as MessagePart & { frames?: SSEEngineProgressEvent[] }).frames ?? []) as SSEEngineProgressEvent[];
        const ended = options.status ? !['running', 'queued', 'suspended'].includes(options.status) : true;
        for (const step of engineSteps(frames, ended)) current().entries.push(engineEntry(step, ended, t));
        break;
      }
      case 'sub_agent': {
        // A code task's child is the hand-off its tool row already shows: the goal goes under that row.
        const handOff = current().entries.find((e) => e.title === t('ui.trail.handedOff') && e.kind === 'tool' && !e.detail);
        if (part.codeTaskGoal && handOff) {
          handOff.detail = plainDetail(part.codeTaskGoal);
          break;
        }
        current().entries.push({
          id: `child-${part.runId ?? current().entries.length}`,
          kind: 'step',
          event: 'StepStarted',
          title: part.codeTaskGoal || part.codeTaskHandoff ? t('ui.trail.handedOff') : t('ui.trail.helper'),
          ...(part.codeTaskGoal ? { detail: plainDetail(part.codeTaskGoal) } : {}),
          icon: part.codeTaskGoal ? 'terminal' : 'group_work',
          ms: 0,
          state: part.status === 'failed' ? 'failed' : part.status === 'running' ? 'running' : 'done',
        });
        break;
      }
      default:
        break;
    }
  }
  const status = jobStatusOf(options.status ?? 'completed', { openAsk });
  return { status, ...shape(raw) };
}
