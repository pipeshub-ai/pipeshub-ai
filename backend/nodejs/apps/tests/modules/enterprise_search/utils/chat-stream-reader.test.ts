import { expect } from 'chai'
import sinon from 'sinon'
import { Logger } from '../../../../src/libs/services/logger.service'
import { AIStreamHooks, readAIStreamChunk } from '../../../../src/modules/enterprise_search/utils/chat-stream-reader'
import { CHAT_ERROR_MESSAGES } from '../../../../src/modules/enterprise_search/utils/chat-error-messages'
import { StreamedContentAccumulator } from '../../../../src/modules/enterprise_search/utils/stream-lifecycle'

const frame = (type: string, fields: Record<string, unknown> = {}): string =>
  `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`

const makeRes = () => ({ write: sinon.stub(), flush: sinon.stub() })

const makeHooks = () => ({
  onAnswer: sinon.stub(),
  onRunError: sinon.stub(),
  onAskUserQuestion: sinon.stub(),
}) satisfies Record<keyof AIStreamHooks, sinon.SinonStub>

const read = (
  text: string,
  hooks: AIStreamHooks = makeHooks(),
  { buffer = '', accumulator }: { buffer?: string; accumulator?: StreamedContentAccumulator } = {},
) => {
  const res = makeRes()
  const tail = readAIStreamChunk(Buffer.from(text), buffer, res as never, accumulator, hooks)
  const written = res.write.getCalls().map((c) => c.args[0] as string)
  return { res, tail, written }
}

interface Rec { level: string; message: string; meta: Record<string, unknown> }
let recs: Rec[]
const badFrames = (): unknown[][] =>
  recs.filter((r) => r.message === 'chat_stream.bad_frame').map((r) => [r.level, r.meta.frame])

describe('readAIStreamChunk', () => {
  beforeEach(() => {
    recs = []
    for (const level of ['error', 'warn', 'info', 'debug'] as const) {
      sinon.stub(Logger.prototype, level).callsFake(function (message: string, meta?: unknown) {
        recs.push({ level, message, meta: (meta ?? {}) as Record<string, unknown> })
      } as never)
    }
  })
  afterEach(() => sinon.restore())

  it('hands the root answer to onAnswer and does not forward it', () => {
    const hooks = makeHooks()
    const result = { answer: 'Done.', citations: [] }
    const { written } = read(frame('RUN_FINISHED', { runId: 'r', result }), hooks)
    expect(hooks.onAnswer.firstCall.args[0]).to.deep.equal(result)
    expect(written).to.deep.equal([])
  })

  it('reads the root RUN_ERROR once for every route and forwards it after the hook', () => {
    const hooks = makeHooks()
    const raw = frame('RUN_ERROR', { runId: 'r', message: 'Too long', code: 'context_length_exceeded', stack: 'Trace' })
    const { written } = read(raw, hooks)
    expect(hooks.onRunError.firstCall.args[0]).to.deep.equal({
      message: 'Too long',
      stack: 'Trace',
      data: { type: 'RUN_ERROR', runId: 'r', message: 'Too long', code: 'context_length_exceeded', stack: 'Trace' },
    })
    expect(written).to.deep.equal([raw])
  })

  it('gives a RUN_ERROR without a usable message the generic text and no stack', () => {
    const hooks = makeHooks()
    read(frame('RUN_ERROR', { runId: 'r', message: '', stack: 7 }), hooks)
    const error = hooks.onRunError.firstCall.args[0]
    expect([error.message, error.stack]).to.deep.equal([CHAT_ERROR_MESSAGES.failed, undefined])
  })

  it('still forwards a RUN_ERROR whose hook throws, and logs it as a bad frame', () => {
    const hooks = makeHooks()
    hooks.onRunError.throws(new Error('boom'))
    const raw = frame('RUN_ERROR', { runId: 'r', message: 'x' })
    expect(read(raw, hooks).written).to.deep.equal([raw])
    expect(badFrames()).to.deep.equal([['warn', 'RUN_ERROR']])
  })

  it("forwards a sub-agent's RUN_FINISHED and RUN_ERROR without calling any hook", () => {
    const hooks = makeHooks()
    const raw =
      frame('RUN_FINISHED', { runId: 'c', parentRunId: 'r', result: { answer: 'child', citations: [] } }) +
      frame('RUN_ERROR', { runId: 'c', parentRunId: 'r', message: 'child failed' })
    expect(read(raw, hooks).written).to.deep.equal([raw])
    expect(hooks.onAnswer.called || hooks.onRunError.called).to.equal(false)
  })

  it('drops an unreadable RUN_FINISHED or RUN_ERROR without calling any hook', () => {
    const hooks = makeHooks()
    const { written } = read('event: RUN_FINISHED\ndata: \n\nevent: RUN_ERROR\ndata: null\n\n', hooks)
    expect(written).to.deep.equal([])
    expect(hooks.onAnswer.called || hooks.onRunError.called).to.equal(false)
    expect(badFrames()).to.deep.equal([['warn', 'RUN_FINISHED'], ['warn', 'RUN_ERROR']])
  })

  it('passes the ask_user_question payload, its toolData first, and forwards the frame', () => {
    const hooks = makeHooks()
    const raw =
      frame('CUSTOM', { name: 'ask_user_question', value: { toolData: { question: 'Q1' } } }) +
      frame('CUSTOM', { name: 'ask_user_question', value: { question: 'Q2' } }) +
      frame('CUSTOM', { name: 'ask_user_question', value: null }) +
      frame('CUSTOM', { name: 'retrieval_context', value: {} })
    expect(read(raw, hooks).written).to.deep.equal([raw])
    expect(hooks.onAskUserQuestion.getCalls().map((c) => c.args[0])).to.deep.equal([{ question: 'Q1' }, { question: 'Q2' }])
  })

  it('forwards a CUSTOM frame whose data does not parse, and logs it as a bad frame', () => {
    const raw = 'event: CUSTOM\ndata: {not json\n\n'
    expect(read(raw).written).to.deep.equal([raw])
    expect(badFrames()).to.deep.equal([['debug', 'CUSTOM']])
  })

  it('forwards a question whose hook throws, and logs it as a bad frame', () => {
    const hooks = makeHooks()
    hooks.onAskUserQuestion.throws(new Error('boom'))
    const raw = frame('CUSTOM', { name: 'ask_user_question', value: { q: 1 } })
    expect(read(raw, hooks).written).to.deep.equal([raw])
    expect(badFrames()).to.deep.equal([['debug', 'CUSTOM']])
  })

  it('feeds text deltas and approved tool calls to the accumulator and forwards them', () => {
    const accumulator = new StreamedContentAccumulator()
    const raw =
      frame('TOOL_CALL_START', { toolCallId: 'a1', toolCallName: 'jira', approved: true }) +
      frame('TEXT_MESSAGE_CONTENT', { runId: 'r', delta: 'Hello' }) +
      'event: TEXT_MESSAGE_CONTENT\ndata: {not json\n\n'
    expect(read(raw, makeHooks(), { accumulator }).written).to.deep.equal([raw])
    expect(badFrames(), 'a text delta that does not parse is not a bad frame').to.deep.equal([])
    expect(accumulator.getText()).to.equal('Hello')
    expect(accumulator.getParts().map((p) => p.toolCallId)).to.deep.equal(['a1'])
  })

  it('forwards every other frame byte for byte, and skips blank ones', () => {
    const raw = 'event: STATE_SNAPSHOT\ndata:{"a":\ndata: 1}\n\ndata: {"no":"event"}\n\nevent: complete\ndata: {}\n\n'
    const { written } = read(raw + '\n\n  \n\n')
    expect(written).to.deep.equal([raw])
  })

  it('keeps an unfinished frame as the tail and writes nothing for it', () => {
    const { written, tail } = read('event: TEXT_MESSAGE_CONTENT\ndata: {"de')
    expect(written).to.deep.equal([])
    expect(tail).to.equal('event: TEXT_MESSAGE_CONTENT\ndata: {"de')
  })

  it('completes a frame from the previous tail, and does not split on \\r\\n', () => {
    const { written, tail } = read('lta":"x"}\n\nevent: A\r\ndata: 1\r\n\r\n', makeHooks(), {
      buffer: 'event: TEXT_MESSAGE_CONTENT\ndata: {"de',
    })
    expect(written).to.deep.equal(['event: TEXT_MESSAGE_CONTENT\ndata: {"delta":"x"}\n\n'])
    expect(tail).to.equal('event: A\r\ndata: 1\r\n\r\n')
  })

  it('calls the hooks in frame order and writes once per chunk', () => {
    const order: string[] = []
    const hooks: AIStreamHooks = {
      onAnswer: () => order.push('answer'),
      onRunError: () => order.push('error'),
      onAskUserQuestion: () => order.push('ask'),
    }
    const { res } = read(
      frame('CUSTOM', { name: 'ask_user_question', value: { q: 1 } }) +
        frame('RUN_ERROR', { runId: 'r', message: 'x' }) +
        frame('RUN_FINISHED', { runId: 'r', result: { answer: 'a', citations: [] } }),
      hooks,
    )
    expect(order).to.deep.equal(['ask', 'error', 'answer'])
    expect(res.write.callCount).to.equal(1)
    expect(res.flush.callCount).to.equal(1)
  })
})
