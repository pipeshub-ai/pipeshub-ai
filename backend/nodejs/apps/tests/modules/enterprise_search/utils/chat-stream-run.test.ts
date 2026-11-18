import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { EventEmitter } from 'events'
import type { Request } from 'express'
import { Logger } from '../../../../src/libs/services/logger.service'
import {
  getRequestContext,
  runWithRequestContext,
  RequestContextStore,
} from '../../../../src/libs/context/request-context'
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
  PaymentRequiredError,
} from '../../../../src/libs/errors/http.errors'
import * as errorClass from '../../../../src/libs/utils/error-class'
import type { IAIResponse } from '../../../../src/modules/enterprise_search/types/conversation.interfaces'
import {
  ChatStreamRun,
  logBadFrame,
  StreamEnd,
} from '../../../../src/modules/enterprise_search/utils/chat-stream-run'

interface Rec {
  level: string
  message: string
  meta: Record<string, unknown>
}

type FakeRes = EventEmitter & { writableEnded: boolean }

const makeRes = (): FakeRes => Object.assign(new EventEmitter(), { writableEnded: false })
const req = {} as Request
const answer = (overrides: Partial<IAIResponse> = {}): IAIResponse =>
  ({ answer: 'ok', citations: [], answerMatchType: 'Exact Match', ...overrides }) as IAIResponse
const refused = (status: number): Error =>
  Object.assign(new Error('Service said no'), { response: { status } })
const unavailable = (): Error =>
  Object.assign(new TypeError('terminated'), { cause: { code: 'UND_ERR_SOCKET' } })

describe('ChatStreamRun', () => {
  let recs: Rec[]

  beforeEach(() => {
    recs = []
    for (const level of ['error', 'warn', 'info', 'debug'] as const) {
      sinon.stub(Logger.prototype, level).callsFake(function (message: string, meta?: unknown) {
        recs.push({ level, message, meta: (meta ?? {}) as Record<string, unknown> })
      } as never)
    }
  })
  afterEach(() => sinon.restore())

  const ends = (): Rec[] => recs.filter((r) => r.message === 'chat_stream.end')
  const onlyEnd = (): Rec => {
    expect(ends()).to.have.length(1)
    return ends()[0] as Rec
  }

  describe('observe', () => {
    it('counts bytes, not characters', () => {
      const run = new ChatStreamRun(req, makeRes())
      run.observe('é€')
      run.observe(Buffer.from('ab'))
      run.end({ kind: 'answered' })
      expect(onlyEnd().meta.bytes).to.equal(Buffer.byteLength('é€') + 2)
    })

    it('counts frame terminators inside a chunk, across two chunks, and back to back', () => {
      const run = new ChatStreamRun(req, makeRes())
      run.observe('event: A\ndata: 1\n\nevent: B\ndata: 2\n')
      run.observe('\nevent: C\n\n\n\n')
      run.end({ kind: 'answered' })
      expect(onlyEnd().meta.events).to.equal(4)
    })

    it('does not count one lone newline at a chunk end twice', () => {
      const run = new ChatStreamRun(req, makeRes())
      run.observe('a\n')
      run.observe('b\n')
      run.observe('\n')
      run.end({ kind: 'answered' })
      expect(onlyEnd().meta.events).to.equal(1)
    })

    it('sets the first-byte time once', () => {
      const clock = sinon.useFakeTimers({ now: 1000, toFake: ['Date'] })
      const run = new ChatStreamRun(req, makeRes())
      clock.tick(30)
      run.observe('a')
      clock.tick(50)
      run.observe('b')
      clock.tick(20)
      run.end({ kind: 'answered' })
      expect(onlyEnd().meta).to.include({ ttfb_ms: 30, duration_ms: 100 })
    })

    it('leaves the first-byte time out when nothing arrived', () => {
      const run = new ChatStreamRun(req, makeRes())
      run.end({ kind: 'answered' })
      expect(onlyEnd().meta.ttfb_ms).to.equal(undefined)
      expect(onlyEnd().meta).to.include({ events: 0, bytes: 0 })
    })
  })

  describe('end: answered', () => {
    const cases: Array<{ name: string; feed: (run: ChatStreamRun) => void; expected: Record<string, unknown>; level: string }> = [
      {
        name: 'a Stop is cancelled at INFO',
        feed: (run) => run.sawResult(answer({ status: 'stopped' })),
        expected: { reason: 'cancelled', outcome: 'partial' },
        level: 'info',
      },
      {
        name: 'a classified failure answer is an upstream error with its code',
        feed: (run) => run.sawResult(answer({ answerMatchType: 'Error' as never, errorCode: 'llm_provider_error' })),
        expected: { reason: 'upstream_error', outcome: 'failure', error_class: 'upstream_5xx', code: 'llm_provider_error' },
        level: 'warn',
      },
      {
        name: 'an answer is completed at INFO',
        feed: (run) => run.sawResult(answer()),
        expected: { reason: 'completed', outcome: 'success' },
        level: 'info',
      },
      {
        name: 'a RUN_ERROR is an upstream error with its code',
        feed: (run) => run.sawRunError(' llm_config '),
        expected: { reason: 'upstream_error', outcome: 'failure', error_class: 'upstream_5xx', code: 'llm_config' },
        level: 'warn',
      },
      {
        name: 'neither is no_response',
        feed: () => undefined,
        expected: { reason: 'upstream_error', outcome: 'failure', error_class: 'upstream_5xx', code: 'no_response' },
        level: 'warn',
      },
      {
        name: 'the last result wins',
        feed: (run) => {
          run.sawResult(answer({ status: 'stopped' }))
          run.sawResult(answer())
        },
        expected: { reason: 'completed' },
        level: 'info',
      },
      {
        name: 'an answer after a RUN_ERROR is completed at INFO',
        feed: (run) => {
          run.sawRunError('llm_config')
          run.sawResult(answer())
        },
        expected: { reason: 'completed', outcome: 'success' },
        level: 'info',
      },
      {
        name: 'the first RUN_ERROR code wins',
        feed: (run) => {
          run.sawRunError('first_code')
          run.sawRunError('second_code')
        },
        expected: { code: 'first_code' },
        level: 'warn',
      },
      {
        name: 'free text as a code is logged as unknown_error',
        feed: (run) => run.sawRunError('The provider said: no'),
        expected: { code: 'unknown_error' },
        level: 'warn',
      },
      {
        name: 'a code that is not a string is logged as unknown_error',
        feed: (run) => run.sawRunError({ nested: 'x' }),
        expected: { code: 'unknown_error' },
        level: 'warn',
      },
      {
        name: 'an error answer with free text as its code is logged as unknown_error',
        feed: (run) => run.sawResult(answer({ errorCode: 'Quota exceeded for user@example.com' })),
        expected: { code: 'unknown_error' },
        level: 'warn',
      },
    ]
    for (const c of cases) {
      it(c.name, () => {
        const run = new ChatStreamRun(req, makeRes())
        c.feed(run)
        run.end({ kind: 'answered' })
        const end = onlyEnd()
        expect(end.level).to.equal(c.level)
        expect(end.meta).to.include({ ...c.expected, event_name: 'chat_stream.end', route: 'unmatched' })
        expect(end.meta).to.not.have.property('error')
      })
    }
  })

  describe('end: failed', () => {
    const openFailing = async (run: ChatStreamRun, error: Error): Promise<void> => {
      await run.open(() => Promise.reject(error)).catch(() => undefined)
    }

    it("the open's 402 is the call line's to report: INFO upstream_error, not rejected", async () => {
      const run = new ChatStreamRun(req, makeRes())
      const error = new PaymentRequiredError('Quota exceeded')
      await openFailing(run, error)
      run.end({ kind: 'failed', error })
      expect(onlyEnd().level).to.equal('info')
      expect(onlyEnd().meta).to.include({ reason: 'upstream_error', outcome: 'failure', error_class: 'bad_request' })
      expect(onlyEnd().meta).to.not.have.property('error')
    })

    it("the open's answered 5xx is INFO upstream_error with its class", async () => {
      const run = new ChatStreamRun(req, makeRes())
      const error = refused(500)
      await openFailing(run, error)
      run.end({ kind: 'failed', error })
      expect(onlyEnd().level).to.equal('info')
      expect(onlyEnd().meta).to.include({ reason: 'upstream_error', error_class: 'upstream_5xx' })
    })

    it('a stream error classed unavailable is INFO upstream_error', () => {
      const run = new ChatStreamRun(req, makeRes())
      run.end({ kind: 'failed', error: unavailable(), fromStream: true })
      expect(onlyEnd().level).to.equal('info')
      expect(onlyEnd().meta).to.include({ reason: 'upstream_error', error_class: 'unavailable' })
    })

    it("the open's own failure (class internal) is ERROR internal_error with the error", async () => {
      const run = new ChatStreamRun(req, makeRes())
      const error = new InternalServerError('Blocked URL')
      await openFailing(run, error)
      run.end({ kind: 'failed', error })
      const end = onlyEnd()
      expect(end.level).to.equal('error')
      expect(end.meta).to.include({ reason: 'internal_error', error_class: 'internal', error_type: 'InternalServerError' })
      expect(end.meta.error).to.equal(error)
    })

    it('a stream error classed misconfigured is ERROR internal_error', () => {
      sinon.stub(errorClass, 'classifyError').returns('misconfigured')
      sinon.stub(errorClass, 'classifyRequestError').returns('misconfigured')
      const run = new ChatStreamRun(req, makeRes())
      const error = new Error('no AI backend configured')
      run.end({ kind: 'failed', error, fromStream: true })
      expect(onlyEnd().level).to.equal('error')
      expect(onlyEnd().meta).to.include({ reason: 'internal_error', error_class: 'misconfigured' })
    })

    it("a refused connection that is not the open's (our database) is ERROR internal_error", async () => {
      const run = new ChatStreamRun(req, makeRes())
      await openFailing(run, refused(500))
      const error = Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:27017'), { cause: { code: 'ECONNREFUSED' } })
      run.end({ kind: 'failed', error })
      expect(onlyEnd().level).to.equal('error')
      expect(onlyEnd().meta).to.include({ reason: 'internal_error', error_class: 'internal', error_type: 'Error' })
    })

    for (const [error, errorClassName] of [
      [new NotFoundError('Conversation not found'), 'not_found'],
      [new BadRequestError('Can only regenerate the last message'), 'bad_request'],
    ] as const) {
      it(`our own ${error.name} is WARN rejected with its type and no error`, () => {
        const run = new ChatStreamRun(req, makeRes())
        run.end({ kind: 'failed', error })
        const end = onlyEnd()
        expect(end.level).to.equal('warn')
        expect(end.meta).to.include({ reason: 'rejected', outcome: 'failure', error_class: errorClassName, error_type: error.name })
        expect(end.meta).to.not.have.property('error')
      })
    }

    it('a thrown non-error is ERROR internal_error without a type', () => {
      const run = new ChatStreamRun(req, makeRes())
      run.end({ kind: 'failed', error: 'boom' })
      expect(onlyEnd().level).to.equal('error')
      expect(onlyEnd().meta).to.include({ reason: 'internal_error', error_class: 'internal' })
      expect(onlyEnd().meta.error_type).to.equal(undefined)
    })
  })

  describe('end: abandoned, client left, once', () => {
    it('the cap is WARN client_disconnect partial detached_cap', () => {
      const run = new ChatStreamRun(req, makeRes())
      run.end({ kind: 'abandoned' })
      expect(onlyEnd().level).to.equal('warn')
      expect(onlyEnd().meta).to.include({ reason: 'client_disconnect', outcome: 'partial', code: 'detached_cap' })
    })

    it('a browser that left before the response ended makes the reason client_disconnect, the rest stays', () => {
      const res = makeRes()
      const run = new ChatStreamRun(req, res)
      run.sawRunError('llm_config')
      res.emit('close')
      run.end({ kind: 'answered' })
      expect(onlyEnd().level).to.equal('warn')
      expect(onlyEnd().meta).to.include({ reason: 'client_disconnect', outcome: 'failure', code: 'llm_config' })
    })

    it('a close after the response ended is not the client leaving', () => {
      const res = makeRes()
      const run = new ChatStreamRun(req, res)
      run.sawResult(answer())
      res.writableEnded = true
      res.emit('close')
      run.end({ kind: 'answered' })
      expect(onlyEnd().meta.reason).to.equal('completed')
    })

    it('writes once: a second end of any kind writes nothing', () => {
      const run = new ChatStreamRun(req, makeRes())
      const later: StreamEnd[] = [{ kind: 'answered' }, { kind: 'failed', error: new Error('late') }, { kind: 'abandoned' }]
      run.end({ kind: 'abandoned' })
      for (const how of later) run.end(how)
      expect(ends()).to.have.length(1)
    })

    it('never throws when the logger throws', () => {
      ;(Logger.prototype.warn as sinon.SinonStub).throws(new Error('transport down'))
      const run = new ChatStreamRun(req, makeRes())
      expect(() => run.end({ kind: 'answered' })).to.not.throw()
    })
  })

  describe('open', () => {
    it('returns what the start returns', async () => {
      const run = new ChatStreamRun(req, makeRes())
      expect(await run.open(() => Promise.resolve('stream'))).to.equal('stream')
    })

    it('rethrows the same error', async () => {
      const run = new ChatStreamRun(req, makeRes())
      const error = new Error('refused')
      let caught: unknown
      await run.open(() => Promise.reject(error)).catch((e: unknown) => {
        caught = e
      })
      expect(caught).to.equal(error)
    })
  })

  describe('bind', () => {
    it('binds only the ids given', () => {
      const store: RequestContextStore = { rootId: 'rq-1', logFields: {} }
      runWithRequestContext(store, () => {
        new ChatStreamRun(req, makeRes()).bind({ conversationId: '65f0c0ffee0000000000abcd' })
      })
      expect(store.logFields).to.deep.equal({ conversation_id: '65f0c0ffee0000000000abcd' })
    })

    it('binds the agent too', () => {
      const store: RequestContextStore = { rootId: 'rq-2', logFields: {} }
      runWithRequestContext(store, () => {
        new ChatStreamRun(req, makeRes()).bind({ conversationId: 'c1', agentKey: 'agent-1' })
      })
      expect(store.logFields).to.deep.equal({ conversation_id: 'c1', agent_id: 'agent-1' })
    })

    it('is a no-op outside a request', () => {
      expect(getRequestContext()).to.equal(undefined)
      expect(() => new ChatStreamRun(req, makeRes()).bind({ conversationId: 'c1' })).to.not.throw()
    })
  })

  describe('logBadFrame', () => {
    it('logs the frame type and its size in bytes, never its data', () => {
      const dataLine = '{"answer": "secret é'
      logBadFrame('RUN_FINISHED', dataLine)
      expect(recs).to.deep.equal([
        {
          level: 'warn',
          message: 'chat_stream.bad_frame',
          meta: { frame: 'RUN_FINISHED', bytes: Buffer.byteLength(dataLine), event_name: 'chat_stream.bad_frame' },
        },
      ])
    })

    it('logs a bad CUSTOM frame at DEBUG: it changes no outcome', () => {
      logBadFrame('CUSTOM', '{')
      expect(recs.map((r) => r.level)).to.deep.equal(['debug'])
    })
  })
})
