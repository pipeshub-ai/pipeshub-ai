import { expect } from 'chai'
import sinon from 'sinon'
import { EventEmitter } from 'events'
import { Readable } from 'stream'
import { Logger } from '../../../../src/libs/services/logger.service'
import {
  getRequestContext,
  runWithRequestContext,
  RequestContextStore,
} from '../../../../src/libs/context/request-context'
import {
  attachUpstreamAbort,
  runGuarded,
  StreamedContentAccumulator,
} from '../../../../src/modules/enterprise_search/utils/stream-lifecycle'

/**
 * Minimal stand-in for Express's `Response` — an EventEmitter plus the two
 * writable-state flags `attachUpstreamAbort` checks to tell "we finished
 * the response ourselves" apart from "the client is actually gone".
 * Defaults to a still-streaming response (both `false`), matching a
 * genuine disconnect scenario unless a test opts into `finish()`.
 */
function createMockRes(): EventEmitter & {
  writableEnded: boolean
  writableFinished: boolean
  finish: () => void
} {
  const res = new EventEmitter() as EventEmitter & {
    writableEnded: boolean
    writableFinished: boolean
    finish: () => void
  }
  res.writableEnded = false
  res.writableFinished = false
  // Mirrors real `res.end()` timing: both flags flip true before 'close' fires.
  res.finish = () => {
    res.writableEnded = true
    res.writableFinished = true
  }
  return res
}

describe('stream-lifecycle', () => {
  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // attachUpstreamAbort
  // -----------------------------------------------------------------------
  describe('attachUpstreamAbort', () => {
    const CAP_MS = 1000

    it('reports the client gone on close but leaves the run alone', () => {
      const res = createMockRes()
      const onAbandon = sinon.stub()
      const handle = attachUpstreamAbort(res, onAbandon, CAP_MS)
      const stream = new Readable({ read() {} })
      const destroySpy = sinon.spy(stream, 'destroy')
      handle.bindStream(stream)

      expect(handle.isClientDisconnected()).to.be.false

      res.emit('close')

      expect(handle.isClientDisconnected()).to.be.true
      expect(handle.isAborted()).to.be.false
      expect(handle.signal.aborted).to.be.false
      expect(destroySpy.called).to.be.false
      expect(onAbandon.called).to.be.false
    })

    it('abandons a still-open stream once the detached-run cap passes', () => {
      const clock = sinon.useFakeTimers()
      const res = createMockRes()
      const onAbandon = sinon.stub()
      const handle = attachUpstreamAbort(res, onAbandon, CAP_MS)
      const stream = new Readable({ read() {} })
      const destroySpy = sinon.spy(stream, 'destroy')
      handle.bindStream(stream)

      res.emit('close')
      clock.tick(CAP_MS - 1)
      expect(onAbandon.called).to.be.false

      clock.tick(1)

      expect(handle.isAborted()).to.be.true
      expect(handle.signal.aborted).to.be.true
      expect(destroySpy.calledOnce).to.be.true
      expect(onAbandon.calledOnce).to.be.true
    })

    it('does not abandon a run whose stream already ended', () => {
      const clock = sinon.useFakeTimers()
      const res = createMockRes()
      const onAbandon = sinon.stub()
      const handle = attachUpstreamAbort(res, onAbandon, CAP_MS)
      const stream = new Readable({ read() {} })
      handle.bindStream(stream)

      res.emit('close')
      stream.destroy()
      clock.tick(CAP_MS * 2)

      expect(handle.isAborted()).to.be.false
      expect(onAbandon.called).to.be.false
    })

    it('finalizes as abandoned at the cap even when no stream is bound yet, so the run is not recorded as failed', () => {
      const clock = sinon.useFakeTimers()
      const res = createMockRes()
      const onAbandon = sinon.stub()
      const handle = attachUpstreamAbort(res, onAbandon, CAP_MS)

      res.emit('close')
      clock.tick(CAP_MS * 2)

      expect(handle.isAborted()).to.be.true
      expect(onAbandon.calledOnce).to.be.true
    })

    it('arms the cap only once even if close fires more than once', () => {
      const clock = sinon.useFakeTimers()
      const res = createMockRes()
      const onAbandon = sinon.stub()
      const handle = attachUpstreamAbort(res, onAbandon, CAP_MS)
      handle.bindStream(new Readable({ read() {} }))

      res.emit('close')
      res.emit('close')
      clock.tick(CAP_MS * 2)

      expect(onAbandon.calledOnce).to.be.true
    })

    // Regression coverage: `IncomingMessage` ('req') fires 'close' as soon
    // as a small POST body finishes being read, unrelated to whether the
    // SSE response is done — see the module doc comment. `res.on('close')`
    // fires on normal completion too, so `writableEnded`/`writableFinished`
    // must gate it or every successful stream would look like a disconnect.
    it('does NOT report disconnected when close fires after the response finished normally', () => {
      const clock = sinon.useFakeTimers()
      const res = createMockRes()
      const onAbandon = sinon.stub()
      const handle = attachUpstreamAbort(res, onAbandon, CAP_MS)
      handle.bindStream(new Readable({ read() {} }))

      res.finish() // mirrors res.end() having already run
      res.emit('close')
      clock.tick(CAP_MS * 2)

      expect(handle.isClientDisconnected()).to.be.false
      expect(handle.isAborted()).to.be.false
      expect(onAbandon.called).to.be.false
    })

    it('treats close as normal completion when only writableFinished is true (checks either flag, not just writableEnded)', () => {
      const res = createMockRes()
      res.writableEnded = false
      res.writableFinished = true
      const handle = attachUpstreamAbort(res, undefined, CAP_MS)

      res.emit('close')

      expect(handle.isClientDisconnected()).to.be.false
    })

    it('logs the client leaving at DEBUG, in the request it was attached in, though close is emitted outside it', () => {
      const seen: Array<RequestContextStore | undefined> = []
      const debug = sinon.stub(Logger.prototype, 'debug').callsFake(() => {
        seen.push(getRequestContext())
      })
      const info = sinon.stub(Logger.prototype, 'info')
      const res = createMockRes()
      const store: RequestContextStore = { rootId: 'rq-attach', logFields: {} }
      runWithRequestContext(store, () => attachUpstreamAbort(res, undefined, CAP_MS))

      res.emit('close')

      expect(debug.calledOnce).to.be.true
      expect(debug.firstCall.args[0]).to.match(/^Client disconnected/)
      expect(seen).to.deep.equal([store])
      expect(info.called).to.be.false
    })

    it('runs the close listener directly when it was attached outside any request', () => {
      const seen: Array<RequestContextStore | undefined> = []
      sinon.stub(Logger.prototype, 'debug').callsFake(() => {
        seen.push(getRequestContext())
      })
      const res = createMockRes()
      const handle = attachUpstreamAbort(res, undefined, CAP_MS)

      runWithRequestContext({ rootId: 'rq-other' }, () => res.emit('close'))

      expect(handle.isClientDisconnected()).to.be.true
      expect(seen).to.have.length(1)
      expect(seen[0]?.rootId, 'the emitter context, as before').to.equal('rq-other')
    })

    it('writes no line of its own when the cap fires: the run reports it', () => {
      const clock = sinon.useFakeTimers()
      const warn = sinon.stub(Logger.prototype, 'warn')
      const error = sinon.stub(Logger.prototype, 'error')
      const res = createMockRes()
      const onAbandon = sinon.stub()
      attachUpstreamAbort(res, onAbandon, CAP_MS)

      res.emit('close')
      clock.tick(CAP_MS)

      expect(onAbandon.calledOnce).to.be.true
      expect(warn.called).to.be.false
      expect(error.called).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // StreamedContentAccumulator
  // -----------------------------------------------------------------------
  describe('StreamedContentAccumulator', () => {
    it('starts empty', () => {
      const acc = new StreamedContentAccumulator()
      expect(acc.getText()).to.equal('')
      expect(acc.hasContent()).to.be.false
      expect(acc.getParts()).to.deep.equal([])
    })

    it('keeps the calls a person approved, so a stopped reply that ran one keeps the mark', () => {
      const acc = new StreamedContentAccumulator()
      acc.feedToolCallStart(JSON.stringify({ toolCallId: 'approved_1', toolCallName: 'mcp_jira_create_issue', approved: true }))
      acc.feedToolCallStart(JSON.stringify({ toolCallId: 'call-2', toolCallName: 'search' }))
      acc.feedToolCallStart('not json')
      expect(acc.getParts()).to.deep.equal([
        { type: 'tool_call', toolCallId: 'approved_1', toolName: 'mcp_jira_create_issue', status: 'completed', approved: true },
      ])
    })

    it('appends deltas from TEXT_MESSAGE_CONTENT frames in order', () => {
      const acc = new StreamedContentAccumulator()
      acc.feedTextMessageContent({ runId: 'run-1', delta: 'Hello ' })
      acc.feedTextMessageContent({ runId: 'run-1', delta: 'world' })
      expect(acc.getText()).to.equal('Hello world')
      expect(acc.hasContent()).to.be.true
    })

    it('ignores frames carrying a parentRunId (sub-agent deltas)', () => {
      const acc = new StreamedContentAccumulator()
      acc.feedTextMessageContent({ runId: 'root-run', delta: 'root ' })
      acc.feedTextMessageContent({ runId: 'sub-run', parentRunId: 'root-run', delta: 'sub-agent text' })
      expect(acc.getText()).to.equal('root ')
    })

    it('locks onto the first runId seen and ignores deltas from a different runId', () => {
      const acc = new StreamedContentAccumulator()
      acc.feedTextMessageContent({ runId: 'run-a', delta: 'A' })
      acc.feedTextMessageContent({ runId: 'run-b', delta: 'B' })
      expect(acc.getText()).to.equal('A')
    })

    it('accepts deltas with no runId at all (runId is optional)', () => {
      const acc = new StreamedContentAccumulator()
      acc.feedTextMessageContent({ delta: 'no run id' })
      expect(acc.getText()).to.equal('no run id')
    })

    it('ignores non-string deltas', () => {
      const acc = new StreamedContentAccumulator()
      acc.feedTextMessageContent({ runId: 'run-1', delta: 123 as unknown as string })
      expect(acc.getText()).to.equal('')
    })

    it('setAccumulatedText replaces rather than appends (legacy answer_chunk protocol)', () => {
      const acc = new StreamedContentAccumulator()
      acc.feedTextMessageContent({ runId: 'run-1', delta: 'ignored if replaced' })
      acc.setAccumulatedText('full running text so far')
      expect(acc.getText()).to.equal('full running text so far')
      acc.setAccumulatedText('full running text so far and more')
      expect(acc.getText()).to.equal('full running text so far and more')
    })
  })
})

describe('runGuarded', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('runs the task', async () => {
    const task = sinon.stub().resolves()

    runGuarded(task)
    await new Promise((resolve) => setImmediate(resolve))

    expect(task.calledOnce).to.be.true
  })

  it('swallows a failing task instead of leaving an unhandled rejection', async () => {
    const unhandled = sinon.stub()
    process.once('unhandledRejection', unhandled)
    try {
      runGuarded(() => Promise.reject(new Error('disk full')))
      await new Promise((resolve) => setTimeout(resolve, 20))
    } finally {
      process.removeListener('unhandledRejection', unhandled)
    }

    expect(unhandled.called).to.be.false
  })

  it('logs the failure once at ERROR with the error itself, so its stack is kept', async () => {
    const error = sinon.stub(Logger.prototype, 'error')
    const failure = new Error('disk full')

    runGuarded(() => Promise.reject(failure))
    await new Promise((resolve) => setImmediate(resolve))

    expect(error.calledOnceWithExactly('Failed to finalize the run', { error: failure })).to.be.true
  })
})
