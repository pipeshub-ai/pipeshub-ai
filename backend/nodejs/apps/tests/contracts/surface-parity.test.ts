import 'reflect-metadata'
import mongoose from 'mongoose'
import { expect } from 'chai'
import sinon from 'sinon'
import { SCENARIOS, replay } from './support/golden'
import {
  DURABLE_SCENARIOS,
  GoldenJournal,
  journalOnlySteps,
  liveSteps,
  narratedJournal,
  project,
  readJournal,
  rechunked,
  reclaimedJournal,
  sseBody,
} from './support/durable'
import { McpParser, PersistedRun, loadMcpParser, mcpToolResult, persistedRun, slackRendering, streamFor } from './support/surfaces'
import { AguiFrame } from '../../src/modules/agent-harness/projection/frames'
import { stripModelAuthoredMarkers } from '../../src/modules/agent-harness/projection/text-filter'
import { registerUpstreams } from '../../src/libs/http/upstream-call'
import { registerAppUpstreams } from '../../src/modules/tokens_manager/config/config'
import { appConfig as streamingAppConfig } from '../modules/enterprise_search/controller/streaming-flows'

/**
 * P1b C-5, C-20: an external surface on the durable route gets exactly what
 * its legacy route rendered, for every durable scenario, live and
 * journal-only. An MCP client gets the answer-only audience (its tool result
 * is the finished conversation or the error); the Slack bot gets the `slack`
 * audience, its activity timeline and streamed answer included. Both sides
 * read the conversation back as production does (connected, citations
 * populated): the legacy one through the real chat controller replaying the
 * recorded AI stream, the durable one through the real conversation writer
 * and reader.
 */

const connected = (): void => {
  const connection = mongoose.connection
  sinon
    .stub(mongoose, 'connection')
    .get(() => new Proxy(connection, { get: (target, key) => (key === 'readyState' ? 1 : Reflect.get(target, key)) }))
}

async function bothSides(scenario: string): Promise<{ legacy: string; durable: PersistedRun }> {
  const flow = SCENARIOS.find((s) => s.name === scenario)
  if (flow === undefined) throw new Error(`no legacy flow for ${scenario}`)
  connected()
  const legacy = (await replay(flow)).res.body
  sinon.restore()
  connected()
  const durable = await persistedRun(scenario)
  sinon.restore()
  return { legacy, durable }
}

const ANSWER_DELTA_PATHS = new Set(['/artifacts/-', '/citations', '/normalizedAnswer', '/rawLength', '/confidence'])
const ANSWER_FRAMES = new Set(['CUSTOM', 'RUN_STARTED', 'RUN_ERROR', 'RUN_FINISHED', 'STATE_DELTA', 'STATE_SNAPSHOT'])

/** What the run did on the way, by kind, that some audience must never see. */
interface Steps {
  toolArgs: string[]
  toolResults: string[]
  narration: string[]
  reasoning: string[]
}

const meaningful = (found: string[], journal: GoldenJournal): string[] =>
  [...new Set(found)].filter((text) => text.length >= 8 && text !== journal.query)

// Journal payload fields are strings when present; a missing one reads as empty.
const textOf = (value: unknown): string =>
  typeof value === 'string' ? value : value === undefined || value === null ? '' : JSON.stringify(value)

function stepsOf(journal: GoldenJournal): Steps {
  const answers = journal.events
    .filter((e) => e.type === 'x.pipeshub.answer.finalized')
    .map((e) => textOf(e.data?.['answer']))
  const questionCalls = new Set(
    journal.events.filter((e) => e.type === 'x.pipeshub.question.asked').map((e) => String(e.data?.['callId'])),
  )
  const steps: Steps = { toolArgs: [], toolResults: [], narration: [], reasoning: [] }
  for (const [index, event] of journal.events.entries()) {
    const data = (event.data ?? {}) as Record<string, unknown>
    if (event.type === 'x.pipeshub.tool.call_presented' && !questionCalls.has(String(data['callId']))) {
      steps.toolArgs.push(String(data['argsJson']), textOf(data['argsSummary']))
    }
    if (event.type === 'x.pipeshub.tool.result_presented' && !questionCalls.has(String(data['callId']))) {
      steps.toolResults.push(textOf(data['content']), textOf(data['resultSummary']))
    }
    if (event.type === 'x.pipeshub.message.presented') {
      steps.reasoning.push(textOf(data['reasoning']))
      // Narration: text of a response that is not the answer (the journal marks the final
      // message next; a final_answer call's text is the finalized answer).
      const next = journal.events[index + 1]
      const text = stripModelAuthoredMarkers(textOf(data['text'])).trim()
      const answer =
        (next?.type === 'message.completed' && next.data?.['isFinal'] === true) || answers.some((a) => a.includes(text))
      if (!answer) steps.narration.push(text)
    }
  }
  for (const recorded of journal.live) {
    if (recorded.frame.kind === 'reasoning_delta') steps.reasoning.push(recorded.frame.data.text ?? '')
  }
  return {
    toolArgs: meaningful(steps.toolArgs, journal),
    toolResults: meaningful(steps.toolResults, journal),
    narration: meaningful(steps.narration, journal),
    reasoning: meaningful(steps.reasoning, journal),
  }
}

const allSteps = (steps: Steps): string[] => [...steps.toolArgs, ...steps.toolResults, ...steps.narration, ...steps.reasoning]

const shows = (body: string, secret: string): boolean => body.includes(secret) || body.includes(JSON.stringify(secret).slice(1, -1))

function answerOnlyViolations(frames: AguiFrame[]): string[] {
  const violations: string[] = []
  for (const frame of frames) {
    if (!ANSWER_FRAMES.has(frame.type)) violations.push(frame.type)
    if (frame.type === 'CUSTOM' && frame['name'] !== 'conversation_created') violations.push(`CUSTOM ${String(frame['name'])}`)
    if (frame.type === 'STATE_SNAPSHOT' && (frame['snapshot'] as Record<string, unknown>)['final'] !== true) violations.push('step snapshot')
    if (frame.type === 'STATE_DELTA') {
      for (const op of frame['delta'] as Array<{ path: string }>) {
        if (!ANSWER_DELTA_PATHS.has(op.path)) violations.push(`STATE_DELTA ${op.path}`)
      }
    }
    if (frame.type === 'RUN_FINISHED') violations.push(...finishedViolations(frame))
  }
  return violations
}

function finishedViolations(frame: AguiFrame): string[] {
  const violations: string[] = []
  const conversation = (frame['result'] as { conversation: { messages: Array<Record<string, unknown>> } }).conversation
  for (const message of conversation.messages) {
    if ('reasoning' in message) violations.push('message reasoning')
    for (const part of (message['parts'] as Array<Record<string, unknown>> | undefined) ?? []) {
      if (part['type'] !== 'text' || part['isFinal'] !== true) violations.push(`part ${String(part['type'])}`)
    }
  }
  return violations
}

/**
 * The fields the legacy Slack bot reads of each frame
 * (`integrations/slack-bot/src/utils/agui-stream.ts`), written from its
 * handler independently of the projector's table. `TOOL_CALL_RESULT.content`
 * is read only for the question tool, whose arguments it prefers.
 */
const SLACK_READS: Record<string, readonly string[]> = {
  CUSTOM: ['name', 'value'],
  RUN_STARTED: ['runId', 'parentRunId'],
  RUN_ERROR: ['message', 'parentRunId', 'runId'],
  RUN_FINISHED: ['result', 'parentRunId', 'runId'],
  STATE_DELTA: ['delta'],
  STATE_SNAPSHOT: ['snapshot'],
  TEXT_MESSAGE_START: ['runId'],
  TEXT_MESSAGE_CONTENT: ['delta', 'runId'],
  TEXT_MESSAGE_END: ['runId'],
  REASONING_MESSAGE_CONTENT: ['delta', 'runId'],
  REASONING_END: [],
  TOOL_CALL_START: ['toolCallName', 'displayName', 'toolCallId', 'runId'],
  TOOL_CALL_ARGS: ['toolCallId', 'delta'],
  TOOL_CALL_RESULT: ['toolCallId', 'status', 'runId'],
}
const SLACK_CUSTOM = new Set(['conversation_created', 'ask_user_question', 'message_discarded'])
const SLACK_STEP_SNAPSHOT = new Set(['status', 'current_tool'])

function slackViolations(frames: AguiFrame[], questionCalls: Set<string>): string[] {
  const violations: string[] = []
  for (const frame of frames) {
    const reads = SLACK_READS[frame.type]
    if (reads === undefined) {
      violations.push(frame.type)
      continue
    }
    for (const field of Object.keys(frame)) {
      if (field !== 'type' && !reads.includes(field)) violations.push(`${frame.type}.${field}`)
    }
    if (frame.type === 'CUSTOM' && !SLACK_CUSTOM.has(String(frame['name']))) violations.push(`CUSTOM ${String(frame['name'])}`)
    if (frame.type === 'TOOL_CALL_ARGS' && !questionCalls.has(String(frame['toolCallId']))) violations.push('tool arguments')
    if (frame.type === 'STATE_SNAPSHOT') {
      const snapshot = frame['snapshot'] as Record<string, unknown>
      if (snapshot['final'] !== true) {
        for (const key of Object.keys(snapshot)) if (!SLACK_STEP_SNAPSHOT.has(key)) violations.push(`step snapshot ${key}`)
      }
    }
    if (frame.type === 'STATE_DELTA') {
      for (const op of frame['delta'] as Array<{ path: string }>) {
        if (!ANSWER_DELTA_PATHS.has(op.path)) violations.push(`STATE_DELTA ${op.path}`)
      }
    }
    if (frame.type === 'RUN_FINISHED') violations.push(...finishedViolations(frame))
  }
  return violations
}

const questionCallsOf = (journal: GoldenJournal): Set<string> =>
  new Set(journal.events.filter((e) => e.type === 'x.pipeshub.question.asked').map((e) => String(e.data?.['callId'])))

describe('contract: external surfaces on the durable route (P1b C-5, C-20)', () => {
  beforeEach(() => registerAppUpstreams(streamingAppConfig))
  afterEach(() => registerUpstreams({}))

  let parser: McpParser

  before(async () => {
    parser = await loadMcpParser()
  })

  afterEach(() => {
    sinon.restore()
  })

  for (const scenario of DURABLE_SCENARIOS) {
    it(`SF-2 ${scenario}: the MCP tool result is the same with the flag on and off`, async () => {
      const { legacy: body, durable: persisted } = await bothSides(scenario)
      const legacy = await mcpToolResult(parser, body)
      expect(legacy.terminal, 'the legacy stream ends').to.not.equal(null)
      for (const steps of [liveSteps(persisted.journal), journalOnlySteps(persisted.journal)]) {
        const durable = await mcpToolResult(parser, sseBody(streamFor(persisted, steps, 'external')))
        expect(durable).to.deep.equal(legacy)
      }
    })

    it(`SL-1 ${scenario}: the Slack bot writes the same posts and updates, timeline and streamed answer included`, async () => {
      const { legacy: body, durable: persisted } = await bothSides(scenario)
      const legacy = slackRendering(body)
      expect(legacy.length, 'the bot writes something').to.be.greaterThan(0)
      for (const steps of [liveSteps(persisted.journal), journalOnlySteps(persisted.journal)]) {
        expect(slackRendering(sseBody(streamFor(persisted, steps, 'slack')))).to.deep.equal(legacy)
      }
    })

    it(`SF-4 ${scenario}: MCP gets the answer only, no tool argument, step, narration, reasoning or question card`, async () => {
      const { durable: persisted } = await bothSides(scenario)
      const secrets = allSteps(stepsOf(persisted.journal))
      for (const steps of [liveSteps(persisted.journal), journalOnlySteps(persisted.journal)]) {
        const frames = streamFor(persisted, steps, 'external')
        expect(answerOnlyViolations(frames.map((f) => f.frame))).to.deep.equal([])
        const body = sseBody(frames)
        for (const secret of secrets) expect(shows(body, secret), secret).to.equal(false)
      }
      // The owner's stream carries them, so the sentinel is not vacuous.
      const owner = sseBody(streamFor(persisted, liveSteps(persisted.journal), 'owner'))
      for (const secret of secrets) expect(shows(owner, secret), `owner sees ${secret}`).to.equal(true)
    })

    it(`SL-2 ${scenario}: nothing beyond legacy Slack's exposure reaches Slack: no tool argument or result, no field the bot does not read`, async () => {
      const { durable: persisted } = await bothSides(scenario)
      const { toolArgs, toolResults, narration, reasoning } = stepsOf(persisted.journal)
      for (const steps of [liveSteps(persisted.journal), journalOnlySteps(persisted.journal)]) {
        const frames = streamFor(persisted, steps, 'slack')
        expect(slackViolations(frames.map((f) => f.frame), questionCallsOf(persisted.journal))).to.deep.equal([])
        const body = sseBody(frames)
        for (const secret of [...toolArgs, ...toolResults]) expect(shows(body, secret), secret).to.equal(false)
      }
      // What the bot's timeline shows does reach it (whole in the journal; deltas chunk it live).
      const whole = sseBody(streamFor(persisted, journalOnlySteps(persisted.journal), 'slack'))
      for (const shown of [...narration, ...reasoning]) expect(shows(whole, shown), shown).to.equal(true)
      const owner = sseBody(streamFor(persisted, liveSteps(persisted.journal), 'owner'))
      for (const secret of [...toolArgs, ...toolResults]) expect(shows(owner, secret), `owner sees ${secret}`).to.equal(true)
    })
  }

  /**
   * Journals no legacy recording exists for: the bot renders the `slack`
   * audience as it renders the owner's stream, which the goldens pin
   * frame-equal to legacy (D-5).
   */
  const derived: Array<[string, () => GoldenJournal]> = [
    ['a narrated, reasoning tool turn', narratedJournal],
    ['a run re-claimed mid-message', reclaimedJournal],
    ...DURABLE_SCENARIOS.map((s): [string, () => GoldenJournal] => [`${s} streamed in small chunks`, () => rechunked(readJournal(s), 3)]),
  ]
  for (const [name, journalOf] of derived) {
    it(`SL-3 ${name}: the bot renders the slack audience as it renders the owner's stream, with no tool argument or result`, () => {
      const journal = journalOf()
      const { toolArgs, toolResults } = stepsOf(journal)
      for (const steps of [liveSteps(journal), journalOnlySteps(journal)]) {
        const frames = project(journal, steps, null, 'slack')
        expect(slackRendering(sseBody(frames))).to.deep.equal(slackRendering(sseBody(project(journal, steps))))
        expect(slackViolations(frames.map((f) => f.frame), questionCallsOf(journal))).to.deep.equal([])
        for (const secret of [...toolArgs, ...toolResults]) expect(shows(sseBody(frames), secret), secret).to.equal(false)
      }
    })
  }

  it('SL-3: narration and reasoning reach the Slack timeline above the finished tool', () => {
    const rendered = slackRendering(sseBody(project(narratedJournal(), liveSteps(narratedJournal()), null, 'slack')))
    const timeline = rendered.flatMap((entry) => ('activity' in entry ? [entry.activity] : []))
    expect(timeline[timeline.length - 1]).to.equal(
      '*Thinking*\n_The user wants the leave allowance, so search first._\n\nLet me check the leave policy in the handbook.\n\n• ✓ Handbook search',
    )
  })

  it('the sentinels cover tool arguments, results, narration and reasoning somewhere', () => {
    const all = DURABLE_SCENARIOS.map((s) => stepsOf(readJournal(s)))
    expect(all.some((s) => s.toolArgs.some((a) => a.includes('"query"')))).to.equal(true)
    expect(all.some((s) => s.toolResults.length > 0)).to.equal(true)
    expect(all.some((s) => s.reasoning.some((r) => r.includes('The handbook says')))).to.equal(true)
    expect(stepsOf(narratedJournal()).narration).to.deep.equal(['Let me check the leave policy in the handbook.'])
  })

  it('SL-1: the parity covers a timeline, a streamed answer, a question, an artifact and an error', async () => {
    const kinds = new Set<string>()
    const activity: string[] = []
    for (const scenario of DURABLE_SCENARIOS) {
      const { legacy } = await bothSides(scenario)
      for (const entry of slackRendering(legacy)) {
        kinds.add(Object.keys(entry)[0]!)
        if ('activity' in entry) activity.push(entry.activity)
      }
    }
    expect([...kinds].sort()).to.deep.equal(['activity', 'answer', 'artifact', 'error', 'final', 'question'])
    expect(activity.some((a) => a.includes('✓')), 'a finished tool row').to.equal(true)
    expect(activity.some((a) => a.includes('*Thinking*')), 'reasoning').to.equal(true)
  })
})
