import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as sanitizer from '../../../../src/modules/enterprise_search/utils/answer-sanitizer'
import {
  RELAYED_CUSTOM_EVENTS,
  RELAYED_EVENT_FIELDS,
  StreamRelay,
} from '../../../../src/modules/enterprise_search/utils/relay-events'
import { settle } from '../controller/chat-test-harness'
import { appConfig, delta, finalAnswer, flows, RUN, Run, startStream } from '../controller/streaming-flows'
import { registerUpstreams } from '../../../../src/libs/http/upstream-call'
import { registerAppUpstreams } from '../../../../src/modules/tokens_manager/config/config'

type Row = Record<string, unknown>

const LABEL = { v: 5, direct: { records: ['rec-CANARY-R'] }, complete: true }

/** One relayed frame, parsed; undefined when the relay dropped it. */
const relayed = (relay: StreamRelay, type: string, data: Row | string): Row | undefined => {
  const out = relay.relay(type, typeof data === 'string' ? data : JSON.stringify(data))
  if (out === '') return undefined
  expect(out.startsWith(`event: ${type}\ndata: `), out).to.equal(true)
  expect(out.endsWith('\n\n')).to.equal(true)
  return JSON.parse(out.slice(`event: ${type}\ndata: `.length, -2)) as Row
}

describe('stream relay: what reaches the browser', () => {
  beforeEach(() => registerAppUpstreams(appConfig))
  afterEach(() => registerUpstreams({}))
  afterEach(() => sinon.restore())

  it('the events it forwards are a closed list; a new one is not forwarded until it is named here', () => {
    expect([...RELAYED_EVENT_FIELDS.keys()].sort()).to.deep.equal([
      'CUSTOM', 'HEARTBEAT', 'REASONING_END', 'REASONING_MESSAGE_CONTENT', 'REASONING_MESSAGE_END', 'REASONING_MESSAGE_START',
      'REASONING_START', 'RUN_ERROR', 'RUN_FINISHED', 'RUN_STARTED', 'STATE_DELTA', 'STATE_SNAPSHOT', 'STEP_FINISHED', 'STEP_STARTED',
      'TEXT_MESSAGE_CONTENT', 'TEXT_MESSAGE_END', 'TEXT_MESSAGE_START', 'TOOL_CALL_ARGS', 'TOOL_CALL_END', 'TOOL_CALL_RESULT',
      'TOOL_CALL_START',
    ])
    expect([...RELAYED_CUSTOM_EVENTS].sort()).to.deep.equal([
      'artifact', 'ask_user_question', 'message_discarded', 'retrieval_context', 'run_usage', 'tool_unavailable', 'tools_unavailable',
    ])
  })

  it('also forwards a custom event the edition keeps as a card, and only that one', () => {
    const relay = new StreamRelay((name) => name === 'draft_card_event')
    const value = { name: 'Weekly report', requestedBy: 'u1' }
    expect(relayed(relay, 'CUSTOM', { type: 'CUSTOM', name: 'draft_card_event', value, runId: RUN })?.value).to.deep.equal(value)
    expect(relayed(relay, 'CUSTOM', { type: 'CUSTOM', name: 'other_event', value, runId: RUN })).to.equal(undefined)
  })

  it('drops a label nested in a forwarded custom value, and keeps a field of the same name that is not one', () => {
    const relay = new StreamRelay((name) => name === 'draft_card_event')
    const out = relayed(relay, 'CUSTOM', {
      type: 'CUSTOM', name: 'draft_card_event', runId: RUN,
      value: { provenance: 'sender', usage: { provenance: LABEL, tokens: 3 } },
    })
    expect(out?.value).to.deep.equal({ provenance: 'sender', usage: { tokens: 3 } })
  })

  it('drops an event it does not know, and a custom event it does not name (the label event among them)', () => {
    const relay = new StreamRelay()
    expect(relayed(relay, 'ANSWER_SOURCES', { type: 'ANSWER_SOURCES', value: LABEL })).to.equal(undefined)
    expect(relayed(relay, 'CUSTOM', { type: 'CUSTOM', name: 'answer_sources', value: LABEL, runId: RUN })).to.equal(undefined)
    expect(relayed(relay, 'CUSTOM', { type: 'CUSTOM', name: 'some_new_event', value: {}, runId: RUN })).to.equal(undefined)
    expect(relayed(relay, 'CUSTOM', { type: 'CUSTOM', name: 'agent_mention', value: { type: 'participant', ref: 'p1' } })).to.equal(undefined)
  })

  it('keeps a named custom event whole', () => {
    const relay = new StreamRelay()
    const value = { status: 'success', toolData: { questions: [{ question: 'EU or US?' }] } }
    expect(relayed(relay, 'CUSTOM', { type: 'CUSTOM', name: 'ask_user_question', value, runId: RUN })).to.deep.equal({
      type: 'CUSTOM', name: 'ask_user_question', value, runId: RUN,
    })
  })

  it('strips fields it does not know from an event it forwards', () => {
    const relay = new StreamRelay()
    expect(relayed(relay, 'TOOL_CALL_START', { type: 'TOOL_CALL_START', toolCallId: 't1', toolCallName: 'search', runId: RUN, displayName: 'Searched', provenance: LABEL })).to.deep.equal({
      type: 'TOOL_CALL_START', toolCallId: 't1', toolCallName: 'search', runId: RUN, displayName: 'Searched',
    })
    expect(relayed(relay, 'RUN_ERROR', { type: 'RUN_ERROR', runId: RUN, message: 'Failed', code: 'agent_error', parentRunId: 'p', provenance: LABEL, stack: 'at x' })).to.deep.equal({
      type: 'RUN_ERROR', runId: RUN, message: 'Failed', code: 'agent_error', parentRunId: 'p',
    })
  })

  it("forwards how a settled card's call was answered, and why an approved one didn't run", () => {
    const relay = new StreamRelay()
    const start = { type: 'TOOL_CALL_START', toolCallId: 'c1', toolCallName: 'mcp_jira_create_issue', runId: RUN }
    expect(relayed(relay, 'TOOL_CALL_START', { ...start, approvalAnswer: 'allowed', approvalReason: 'access_lost' })).to.deep.equal({
      ...start, approvalAnswer: 'allowed', approvalReason: 'access_lost',
    })
  })

  it("forwards why a tool failed and that it sent nothing, so the live row words the failure as a reload does", () => {
    const relay = new StreamRelay()
    const result = { type: 'TOOL_CALL_RESULT', toolCallId: 'c1', runId: RUN, content: 'Rate limited', status: 'failed', resultSummary: 'Not sent' }
    expect(relayed(relay, 'TOOL_CALL_RESULT', { ...result, errorCode: 'rate_limited', nothingSent: true, provenance: LABEL })).to.deep.equal({
      ...result, errorCode: 'rate_limited', nothingSent: true,
    })
  })

  it("sends the asker the final snapshot whole, as main did, with its answer data and without the run's label", () => {
    const relay = new StreamRelay()
    const snapshot = { final: true, answer: 'Revenue grew.', citations: [], provenance: LABEL, modelRouting: { escalated: false } }
    expect(relayed(relay, 'STATE_SNAPSHOT', { type: 'STATE_SNAPSHOT', runId: RUN, snapshot })).to.deep.equal({
      type: 'STATE_SNAPSHOT', runId: RUN, snapshot: { final: true, answer: 'Revenue grew.', citations: [], modelRouting: { escalated: false } },
    })
  })

  it("makes the final snapshot's answer and text parts safe, as a stored answer is", () => {
    const relay = new StreamRelay()
    const image = '![chart](https://evil.test/p.png)'
    const snapshot = { final: true, answer: image, parts: [{ type: 'text', content: image, isFinal: true }] }
    const sent = relayed(relay, 'STATE_SNAPSHOT', { type: 'STATE_SNAPSHOT', runId: RUN, snapshot })?.snapshot as Row
    expect(sent.answer).to.equal(sanitizer.sanitizeAnswerMarkdown(image)).and.to.not.include('https://evil.test/p.png)')
    expect((sent.parts as Row[])[0]).to.deep.equal({ type: 'text', content: sanitizer.sanitizeAnswerMarkdown(image), isFinal: true })
  })

  it('sends a progress snapshot whole, as main did, without the run\'s label', () => {
    const relay = new StreamRelay()
    const snapshot = {
      status: 'running_tool', current_tool: 'search', progress: 2, total: 5, progress_message: 'Reading', run_id: RUN,
      agent_id: 'agent-1', goal_description: 'Find the revenue', provenance: LABEL,
    }
    expect(relayed(relay, 'STATE_SNAPSHOT', { type: 'STATE_SNAPSHOT', runId: RUN, snapshot })?.snapshot).to.deep.equal({
      status: 'running_tool', current_tool: 'search', progress: 2, total: 5, progress_message: 'Reading', run_id: RUN,
      agent_id: 'agent-1', goal_description: 'Find the revenue',
    })
  })

  it('keeps only the answer-state paths the client reads', () => {
    const relay = new StreamRelay()
    const ops = [
      { op: 'replace', path: '/normalizedAnswer', value: 'Revenue grew [1].' },
      { op: 'replace', path: '/rawLength', value: 14 },
      { op: 'replace', path: '/provenance', value: LABEL },
      { op: 'add', path: '/artifacts/-', value: { id: 'a1' } },
    ]
    const out = relayed(relay, 'STATE_DELTA', { type: 'STATE_DELTA', runId: RUN, delta: ops })
    expect((out?.delta as Row[]).map((op) => op.path)).to.deep.equal(['/normalizedAnswer', '/rawLength', '/artifacts/-'])
  })

  it('drops a frame it cannot read', () => {
    const relay = new StreamRelay()
    expect(relayed(relay, 'TEXT_MESSAGE_CONTENT', '{"delta": "half')).to.equal(undefined)
    expect(relayed(relay, 'TEXT_MESSAGE_CONTENT', '')).to.equal(undefined)
    expect(relayed(relay, 'TEXT_MESSAGE_CONTENT', '[1, 2]')).to.equal(undefined)
  })

  it('a label field never rides any forwarded event, at any depth of a known field', () => {
    const relay = new StreamRelay()
    for (const type of RELAYED_EVENT_FIELDS.keys()) {
      const out = relay.relay(type, JSON.stringify({ type, runId: RUN, provenance: LABEL, labelHistory: [LABEL] }))
      expect(out.includes('CANARY'), `${type}: ${out}`).to.equal(false)
    }
  })

  it('passes answer text through the answer sanitiser, one state per message', () => {
    const seen: Array<[unknown, string]> = []
    sinon.stub(sanitizer, 'sanitizeAnswerDelta').callsFake((state, text) => {
      seen.push([state, text])
      // A counter stands in for the sanitiser's state: only which state goes with which message is checked.
      return { state: { n: ((state as { n?: number } | undefined)?.n ?? 0) + 1 } as never, out: text.toUpperCase() }
    })
    const relay = new StreamRelay()
    expect(relayed(relay, 'TEXT_MESSAGE_CONTENT', { type: 'TEXT_MESSAGE_CONTENT', messageId: 'm1', delta: 'ab', runId: RUN })?.delta).to.equal('AB')
    expect(relayed(relay, 'TEXT_MESSAGE_CONTENT', { type: 'TEXT_MESSAGE_CONTENT', messageId: 'm1', delta: 'cd', runId: RUN })?.delta).to.equal('CD')
    expect(relayed(relay, 'TEXT_MESSAGE_CONTENT', { type: 'TEXT_MESSAGE_CONTENT', messageId: 'm2', delta: 'ef', runId: RUN })?.delta).to.equal('EF')
    expect(seen).to.deep.equal([[undefined, 'ab'], [{ n: 1 }, 'cd'], [undefined, 'ef']])
  })

  it("keys a message's sanitiser state by its string ids; an id that is not a string counts as none", () => {
    const seen: unknown[] = []
    sinon.stub(sanitizer, 'sanitizeAnswerDelta').callsFake((state, text) => {
      seen.push(state)
      return { state: { n: ((state as { n?: number } | undefined)?.n ?? 0) + 1 } as never, out: text }
    })
    const relay = new StreamRelay()
    const content = (fields: Row) => relayed(relay, 'TEXT_MESSAGE_CONTENT', { type: 'TEXT_MESSAGE_CONTENT', delta: 'x', ...fields })
    content({ messageId: 'm1', runId: RUN })
    content({ messageId: 'm1', runId: 'run-2' })
    content({ messageId: { id: 'm1' }, runId: RUN })
    content({ runId: RUN })
    expect(seen).to.deep.equal([undefined, undefined, undefined, { n: 1 }])
  })

  it('flushes what the sanitiser held back when the message ends', () => {
    sinon.stub(sanitizer, 'sanitizeAnswerDelta').callsFake((_state, text) => ({ state: { held: true } as never, out: text }))
    sinon.stub(sanitizer, 'flushAnswerDelta').callsFake(() => ({ state: {} as never, out: ' tail' }))
    const relay = new StreamRelay()
    relay.relay('TEXT_MESSAGE_CONTENT', JSON.stringify({ type: 'TEXT_MESSAGE_CONTENT', messageId: 'm1', delta: 'Hello', runId: RUN }))
    const out = relay.relay('TEXT_MESSAGE_END', JSON.stringify({ type: 'TEXT_MESSAGE_END', messageId: 'm1', runId: RUN }))
    const frames = out.split('\n\n').filter(Boolean)
    expect(frames).to.have.length(2)
    expect(frames[0]).to.include('"delta":" tail"')
    expect(frames[1]).to.include('TEXT_MESSAGE_END')
  })

  it('sanitises the numbered copy of the answer whole, keeping nothing of it', () => {
    sinon.stub(sanitizer, 'sanitizePartialAnswerMarkdown').callsFake((text: string) => text.replace('https://evil.test/p.png', '#'))
    const relay = new StreamRelay()
    const out = relayed(relay, 'STATE_DELTA', { type: 'STATE_DELTA', runId: RUN, delta: [{ op: 'replace', path: '/normalizedAnswer', value: '![](https://evil.test/p.png)' }] })
    expect((out?.delta as Row[])[0]?.value).to.equal('![](#)')
  })
})

describe('stream relay: every stream the browser reads', () => {
  beforeEach(() => registerAppUpstreams(appConfig))
  afterEach(() => registerUpstreams({}))
  afterEach(() => sinon.restore())

  const drive = async (run: Run): Promise<void> => {
    run.ai.send('CUSTOM', { name: 'answer_sources', value: LABEL, runId: RUN })
    run.ai.send('CUSTOM', { name: 'some_new_event', value: { secret: 'CANARY-new' }, runId: RUN })
    run.ai.send('TEXT_MESSAGE_CONTENT', delta('Revenue grew.', { provenance: LABEL }))
    run.ai.send('STATE_SNAPSHOT', { runId: RUN, snapshot: { final: true, answer: 'Revenue grew.', provenance: LABEL } })
    const final = finalAnswer('Revenue grew.')
    run.ai.send('RUN_FINISHED', { ...final, result: { ...(final.result as Row), provenance: LABEL } })
    run.ai.finish()
    await run.res.ended
    await settle()
  }

  for (const flow of flows) {
    it(`${flow.name}: forwards the answer and drops the label, unknown events and unknown fields`, async () => {
      const run = await startStream(flow)
      await drive(run)
      expect(run.res.body.includes('CANARY'), run.res.body).to.equal(false)
      expect(run.res.body).to.not.include('answer_sources')
      expect(run.res.body).to.not.include('some_new_event')
      const text = run.res.eventsOf('TEXT_MESSAGE_CONTENT')
      expect(text.map((e) => e.data.delta)).to.deep.equal(['Revenue grew.'])
      const snapshots = run.res.eventsOf('STATE_SNAPSHOT')
      expect(snapshots.map((e) => e.data.snapshot)).to.deep.equal([{ final: true, answer: 'Revenue grew.' }])
      expect(run.res.eventsOf('RUN_FINISHED')).to.have.length(1)
    })
  }
})
