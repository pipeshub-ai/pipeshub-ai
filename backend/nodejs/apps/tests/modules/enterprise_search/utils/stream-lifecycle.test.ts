import { expect } from 'chai'
import sinon from 'sinon'
import { EventEmitter } from 'events'
import { Readable } from 'stream'
import {
  attachUpstreamAbort,
  isUpstreamAbortError,
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
    it('aborts the signal and reports disconnected once the response emits close', () => {
      const res = createMockRes()
      const handle = attachUpstreamAbort(res, 'req-1')

      expect(handle.isClientDisconnected()).to.be.false
      expect(handle.signal.aborted).to.be.false

      res.emit('close')

      expect(handle.isClientDisconnected()).to.be.true
      expect(handle.signal.aborted).to.be.true
    })

    it('invokes onDisconnect exactly once even if close fires more than once', () => {
      const res = createMockRes()
      const onDisconnect = sinon.stub()
      attachUpstreamAbort(res, 'req-2', onDisconnect)

      res.emit('close')
      res.emit('close')

      expect(onDisconnect.calledOnce).to.be.true
    })

    it('destroys a bound stream when close fires after bindStream', () => {
      const res = createMockRes()
      const handle = attachUpstreamAbort(res, 'req-3')
      const stream = new Readable({ read() {} })
      const destroySpy = sinon.spy(stream, 'destroy')

      handle.bindStream(stream)
      res.emit('close')

      expect(destroySpy.calledOnce).to.be.true
    })

    it('destroys a stream bound AFTER the client already disconnected (race with a slow executeStream)', () => {
      const res = createMockRes()
      const handle = attachUpstreamAbort(res, 'req-4')

      // Client disconnects before the upstream Readable exists.
      res.emit('close')
      expect(handle.isClientDisconnected()).to.be.true

      const stream = new Readable({ read() {} })
      const destroySpy = sinon.spy(stream, 'destroy')
      handle.bindStream(stream)

      expect(destroySpy.calledOnce).to.be.true
    })

    it('does not re-destroy an already-destroyed stream when binding late', () => {
      const res = createMockRes()
      const handle = attachUpstreamAbort(res, 'req-5')
      res.emit('close')

      const stream = new Readable({ read() {} })
      stream.destroy()
      const destroySpy = sinon.spy(stream, 'destroy')

      expect(() => handle.bindStream(stream)).to.not.throw()
      expect(destroySpy.called).to.be.false
    })

    // Regression coverage: `IncomingMessage` ('req') fires 'close' as soon
    // as a small POST body finishes being read, unrelated to whether the
    // SSE response is done — see the module doc comment. `res.on('close')`
    // fires on normal completion too, so `writableEnded`/`writableFinished`
    // must gate it or every successful stream would look like a disconnect.
    it('does NOT report disconnected when close fires after the response finished normally', () => {
      const res = createMockRes()
      const onDisconnect = sinon.stub()
      const handle = attachUpstreamAbort(res, 'req-6', onDisconnect)

      res.finish() // mirrors res.end() having already run
      res.emit('close')

      expect(handle.isClientDisconnected()).to.be.false
      expect(handle.signal.aborted).to.be.false
      expect(onDisconnect.called).to.be.false
    })

    it('does NOT destroy a bound stream when close fires after normal completion', () => {
      const res = createMockRes()
      const handle = attachUpstreamAbort(res, 'req-7')
      const stream = new Readable({ read() {} })
      const destroySpy = sinon.spy(stream, 'destroy')
      handle.bindStream(stream)

      res.finish()
      res.emit('close')

      expect(destroySpy.called).to.be.false
    })

    it('treats close as normal completion when only writableFinished is true (checks either flag, not just writableEnded)', () => {
      const res = createMockRes()
      res.writableEnded = false
      res.writableFinished = true
      const handle = attachUpstreamAbort(res, 'req-8')

      res.emit('close')

      expect(handle.isClientDisconnected()).to.be.false
    })
  })

  // -----------------------------------------------------------------------
  // isUpstreamAbortError
  // -----------------------------------------------------------------------
  describe('isUpstreamAbortError', () => {
    it('returns true for an error named AbortError', () => {
      const err = new Error('aborted')
      err.name = 'AbortError'
      expect(isUpstreamAbortError(err)).to.be.true
    })

    it('returns true for an error with code ABORT_ERR', () => {
      const err: any = new Error('aborted')
      err.code = 'ABORT_ERR'
      expect(isUpstreamAbortError(err)).to.be.true
    })

    it('returns false for a genuine upstream failure', () => {
      const err = new Error('upstream 500')
      expect(isUpstreamAbortError(err)).to.be.false
    })

    it('returns false for non-object / nullish inputs', () => {
      expect(isUpstreamAbortError(null)).to.be.false
      expect(isUpstreamAbortError(undefined)).to.be.false
      expect(isUpstreamAbortError('AbortError')).to.be.false
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
