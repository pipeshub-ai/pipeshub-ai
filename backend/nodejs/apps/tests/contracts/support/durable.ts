/**
 * Golden journals (`contracts/harness/vectors/journals/`, written by
 * `backend/python/tests/contracts/test_durable_journals.py`) replayed through
 * the Node projector, and the normalization D-5 compares frames under.
 */
import fs from 'fs'
import path from 'path'
import { JournalEvent } from '../../../src/modules/agent-harness/domain/journal'
import { AguiFrame, LiveDelta, ProjectedFrame, formatSseFrame } from '../../../src/modules/agent-harness/projection/frames'
import { ProjectorContext, RunProjector } from '../../../src/modules/agent-harness/projection/run-projector'
import { StreamCursor } from '../../../src/modules/agent-harness/projection/stream-id'
import { stripModelAuthoredMarkers } from '../../../src/modules/agent-harness/projection/text-filter'

export const JOURNALS_DIR = path.join(__dirname, '..', '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'journals')

export interface RecordedDelta {
  afterSeq: number
  frameId: string
  frame: LiveDelta
}

export interface GoldenJournal {
  scenario: string
  query: string
  runId: string
  sessionId: string
  events: JournalEvent[]
  live: RecordedDelta[]
}

export const AGENT_SCENARIOS = [
  'agent_quick_tool_call',
  'agent_react_tool_error_then_success',
  'agent_ask_user_question',
  'agent_artifact_marker_answer',
  'agent_cancel_mid_stream',
  'agent_quick_reasoning',
  'agent_quick_final_answer',
  'agent_final_answer_with_write',
  'agent_search_tools_unavailable',
  'agent_pre_run_clarification',
  'agent_live_file_citation',
]

/** The built-in assistant's scenarios (`chat_llm_error` is a rate limit, which a durable run retries: L-18). */
export const CHAT_SCENARIOS = ['chat_answer_with_citations', 'chat_llm_unavailable', 'chat_llm_rejected']

export const DURABLE_SCENARIOS = [...AGENT_SCENARIOS, ...CHAT_SCENARIOS]

/**
 * Steered runs (P1b T2), durable only (legacy has no steering): the user
 * sends a message while a tool runs, and while the answer streams.
 */
export const STEERED_SCENARIOS: Record<string, string> = {
  agent_steered_during_tool: 'Add Q4 too, if it is closed.',
  agent_steered_after_answer: 'And sick leave?',
}

export const readJournal = (name: string): GoldenJournal =>
  JSON.parse(fs.readFileSync(path.join(JOURNALS_DIR, `${name}.journal.json`), 'utf8')) as GoldenJournal

/**
 * Each recorded message's deltas re-chunked into `pieces` deltas (as a slower
 * provider streams). Consecutive frames of one kind and model attempt are one
 * message's text whatever the worker's coalescing; `n` is gap-free per
 * (fence, attempt), across delta kinds.
 */
export function rechunked(journal: GoldenJournal, pieces: number): GoldenJournal {
  const messages: RecordedDelta[] = []
  for (const recorded of journal.live) {
    const last = messages[messages.length - 1]
    const same =
      last !== undefined &&
      last.frame.fence === recorded.frame.fence &&
      last.frame.modelAttempt === recorded.frame.modelAttempt &&
      last.frame.kind === recorded.frame.kind
    if (same) {
      messages[messages.length - 1] = {
        ...last,
        frame: { ...last.frame, data: { text: `${last.frame.data.text ?? ''}${recorded.frame.data.text ?? ''}` } },
      }
    } else {
      messages.push(recorded)
    }
  }
  const live: RecordedDelta[] = []
  const numbered = new Map<string, number>()
  for (const recorded of messages) {
    const text = recorded.frame.data.text ?? ''
    const size = Math.max(1, Math.ceil(text.length / pieces))
    const parts = text.match(new RegExp(`[\\s\\S]{1,${size}}`, 'g')) ?? [text]
    const key = `${recorded.frame.fence}.${recorded.frame.modelAttempt}`
    parts.forEach((part, index) => {
      const n = (numbered.get(key) ?? 0) + 1
      numbered.set(key, n)
      live.push({
        afterSeq: recorded.afterSeq,
        frameId: `${recorded.frameId}.${index}`,
        frame: { ...recorded.frame, n, data: { text: part } },
      })
    })
  }
  return { ...journal, live }
}

/**
 * The worker dies after committing turn 1's tool outcome, mid turn-2 message;
 * a new worker re-claims at fence 2 and re-samples turn 2 (L-10).
 */
export function reclaimedJournal(): GoldenJournal {
  const base = rechunked(readJournal('agent_quick_tool_call'), 2);
  // The worker dies after committing the tool outcome, before its presentation.
  const split = base.events.find((e) => e.type === 'tool.outcome')!.seq;
  const head = base.events.filter((e) => e.seq <= split);
  const tail = base.events.filter((e) => e.seq > split);
  const claim = { ...base.events.find((e) => e.type === 'run.claimed')! };
  const reclaim: JournalEvent = { ...claim, seq: split + 1, fence: 2, attemptId: 'run-golden.a2', data: { attempt: 2, fence: 2, leaseMs: 30000 } };
  const moved = tail.map((e) => ({ ...e, seq: e.seq + 1, fence: 2, attemptId: 'run-golden.a2' }));
  // Turn 2's message: its text deltas (turn 1 only announced its tool's arguments).
  const old = base.live.filter((d) => d.frame.kind !== 'tool_call').map((d) => d.frame);
  const fresh = old.map((d) => ({ ...d, fence: 2, modelAttempt: 1 }));
  // Attempt 1 of the new fence re-samples turn 2.
  const renumbered = moved.map((e) => (e.type === 'model.request' && e.data?.['modelAttempt'] === 2 ? { ...e, data: { ...e.data, modelAttempt: 1 } } : e));
  return {
    ...base,
    events: [...head, reclaim, ...renumbered],
    live: [
      { afterSeq: split, frameId: 'a', frame: old[0]! },
      { afterSeq: split + 1, frameId: 'b', frame: old[1]! },
      ...fresh.map((frame, i) => ({ afterSeq: split + 1, frameId: `c${i}`, frame })),
    ],
  };
}

/**
 * `agent_quick_tool_call` with the tool turn narrating and reasoning before
 * a call that has a display name (the goldens' tool turns say nothing and
 * name no call): what an activity timeline shows.
 */
export function narratedJournal(): GoldenJournal {
  const base = readJournal('agent_quick_tool_call')
  const turnOne = base.events.find((e) => e.type === 'x.pipeshub.message.presented')!.seq
  const events = base.events.map((e) =>
    e.seq === turnOne
      ? { ...e, data: { text: 'Let me check the leave policy in the handbook.', reasoning: 'The user wants the leave allowance, so search first.' } }
      : e.type === 'x.pipeshub.tool.call_presented'
        ? { ...e, data: { ...e.data, displayName: 'Handbook search' } }
        : e,
  )
  const [call, ...rest] = base.live
  const at = (n: number, kind: LiveDelta['kind'], text: string): RecordedDelta => ({
    afterSeq: call!.afterSeq,
    frameId: `narrated.${n}`,
    frame: { ...call!.frame, n, kind, data: { text } },
  })
  return {
    ...base,
    scenario: 'agent_narrated_tool_call',
    events,
    live: [
      at(1, 'reasoning_delta', 'The user wants the leave allowance, '),
      at(2, 'reasoning_delta', 'so search first.'),
      at(3, 'text_delta', 'Let me check the leave policy '),
      at(4, 'text_delta', 'in the handbook.'),
      at(5, 'tool_call', 'arguments'),
      ...rest,
    ],
  }
}

export const projectorContext = (journal: GoldenJournal, audience: ProjectorContext['audience'] = 'owner'): ProjectorContext => ({
  runId: journal.runId,
  sessionId: journal.sessionId,
  conversationId: journal.sessionId,
  title: journal.query.slice(0, 100),
  goal: journal.query,
  audience,
})

/** One step of what a live subscriber receives: a journal page, or one delta. */
export type Step = { journal: JournalEvent[] } | { delta: LiveDelta }

/** The order a live subscriber saw things in: each delta after the journal head it followed. */
export function liveSteps(journal: GoldenJournal, deltas = journal.live): Step[] {
  const steps: Step[] = []
  let fed = 0
  const feedThrough = (seq: number): void => {
    const page = journal.events.filter((e) => e.seq > fed && e.seq <= seq)
    if (page.length > 0) steps.push({ journal: page })
    fed = Math.max(fed, seq)
  }
  for (const recorded of deltas) {
    feedThrough(recorded.afterSeq)
    steps.push({ delta: recorded.frame })
  }
  feedThrough(Number.MAX_SAFE_INTEGER)
  return steps
}

/** Every journal event at once, no deltas: a Redis-less deployment (L-5). */
export const journalOnlySteps = (journal: GoldenJournal): Step[] => [{ journal: journal.events }]

export const PLACEHOLDER_CONVERSATION = { _id: 'conversation', messages: [] }

export function runSteps(projector: RunProjector, steps: Step[], opts: { begin?: boolean } = {}): ProjectedFrame[] {
  const frames: ProjectedFrame[] = opts.begin === false ? [] : projector.begin()
  for (const step of steps) {
    frames.push(...('journal' in step ? projector.project(step.journal) : projector.delta(step.delta)))
  }
  frames.push(...projector.finish(PLACEHOLDER_CONVERSATION, { requestId: 'r', timestamp: 't', duration: 0 }))
  return frames
}

export const project = (journal: GoldenJournal, steps: Step[], cursor: StreamCursor | null = null, audience: ProjectorContext['audience'] = 'owner'): ProjectedFrame[] =>
  runSteps(new RunProjector(projectorContext(journal, audience), cursor?.position ?? null), steps, { begin: cursor === null })

export const sseBody = (frames: ProjectedFrame[]): string => frames.map(formatSseFrame).join('')

// ---- normalization (D-5: frames equal modulo ids and delta chunking) ----

const UUIDISH = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|uuid-\d+/g
const ID_KEYS = new Set(['runId', 'threadId', 'messageId', 'run_id', 'agent_id', 'trace_id', 'conversationId', 'uuid'])
// `expiresAt`: a question card's expiry, read off the clock when it is asked (legacy goldens hold `<timestamp>`).
const CLOCK_KEYS = new Set(['started_at', 'updated_at', 'expiresAt'])

function normalizeValue(value: unknown, ids: Map<string, string>, key?: string): unknown {
  if (typeof value === 'string') {
    if (key !== undefined && ID_KEYS.has(key)) {
      const label = `${key}:${value}`
      if (!ids.has(label)) ids.set(label, `<${key}-${[...ids.keys()].filter((k) => k.startsWith(`${key}:`)).length + 1}>`)
      return ids.get(label)
    }
    if (key !== undefined && CLOCK_KEYS.has(key)) return '<clock>'
    if (key === 'downloadUrl') return '<url>'
    return value.replace(UUIDISH, '<uuid>')
  }
  if (Array.isArray(value)) return value.map((item) => normalizeValue(item, ids))
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, normalizeValue(v, ids, k)]))
  }
  return value
}

const isAnswerDelta = (frame: AguiFrame): boolean =>
  frame.type === 'STATE_DELTA' && ((frame.delta as Array<{ path: string }>) ?? []).some((op) => op.path === '/normalizedAnswer')

/**
 * Frames under D-5's equivalence: ids and clocks replaced by placeholders;
 * consecutive TEXT_MESSAGE_CONTENT (and REASONING_MESSAGE_CONTENT) of a
 * message merged (chunking is the provider's); only the last answer STATE_DELTA kept (the others track
 * chunking, and legacy repeated its final one: oddity 6); model-authored
 * markers removed from streamed text (the
 * intentional P0.19 change, L-12); Node's conversation payload left to the
 * transcript tests.
 */
export function normalizeFrames(frames: AguiFrame[], opts: { goal?: string } = {}): AguiFrame[] {
  const merged: AguiFrame[] = []
  const lastAnswer = frames.map(isAnswerDelta).lastIndexOf(true)
  for (const [index, frame] of frames.entries()) {
    if (isAnswerDelta(frame) && index !== lastAnswer) continue
    const last = merged[merged.length - 1]
    const chunked = frame.type === 'TEXT_MESSAGE_CONTENT' || frame.type === 'REASONING_MESSAGE_CONTENT'
    if (chunked && last?.type === frame.type && last.messageId === frame.messageId) {
      merged[merged.length - 1] = { ...last, delta: `${String(last.delta)}${String(frame.delta)}` }
      continue
    }
    merged.push(frame)
  }
  const ids = new Map<string, string>()
  return merged.map((frame) => {
    let shaped: AguiFrame = frame
    if (frame.type === 'TEXT_MESSAGE_CONTENT') shaped = { ...frame, delta: stripModelAuthoredMarkers(String(frame.delta)) }
    if (frame.type === 'RUN_FINISHED') shaped = { type: 'RUN_FINISHED', result: '<conversation>' }
    // The one additive difference: the owner's first frame names its run (LAT1), which a legacy turn has none of.
    if (frame.type === 'CUSTOM' && frame.name === 'conversation_created') {
      const value = { ...(frame.value as Record<string, unknown>) }
      delete value['runId']
      shaped = { ...shaped, value }
    }
    if (opts.goal !== undefined && frame.type === 'STATE_SNAPSHOT' && 'goal_description' in (frame.snapshot as Record<string, unknown>)) {
      shaped = { ...shaped, snapshot: { ...(frame.snapshot as Record<string, unknown>), goal_description: opts.goal } }
    }
    return normalizeValue(shaped, ids) as AguiFrame
  }).filter((frame) => !(frame.type === 'TEXT_MESSAGE_CONTENT' && frame.delta === ''))
}

/** The `data:` objects of an SSE body (legacy frames have no `id:` line, durable ones do). */
export function parseSse(body: string): Array<{ id?: string; frame: AguiFrame }> {
  return body
    .split('\n\n')
    .filter((block) => block.trim())
    .map((block) => {
      const lines = block.split('\n')
      const id = lines.find((l) => l.startsWith('id: '))?.slice(4)
      const data = lines.find((l) => l.startsWith('data: ')) as string
      return { ...(id !== undefined ? { id } : {}), frame: JSON.parse(data.slice(6)) as AguiFrame }
    })
}
