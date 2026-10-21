import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ServiceUnavailableError } from '../../../../src/libs/errors/http.errors'
import * as controllerModule from '../../../../src/modules/enterprise_search/controller/es_controller'
import { createConversation } from '../../../../src/modules/enterprise_search/controller/non-streaming-chat.controller'
import {
  drainRuns,
  isShuttingDown,
  refuseRunWhileShuttingDown,
  resetActiveRunsForTests,
  stopAllRuns,
  stopRun,
  trackRun,
} from '../../../../src/modules/enterprise_search/utils/active-runs'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, settle } from '../controller/chat-test-harness'
import { appConfig, delta, finalAnswer, flows, requestFor, startStream } from '../controller/streaming-flows'
import { turnDeps } from '../helpers/turn-deps'

const streamSignal = (): AbortSignal | undefined => {
  const call = (globalThis.fetch as unknown as sinon.SinonStub)
    .getCalls()
    .find((c) => /\/stream$/.test(String(c.args[0])))
  return (call?.args[1] as RequestInit | undefined)?.signal ?? undefined
}

describe('active runs', () => {
  beforeEach(() => {
    resetActiveRunsForTests()
  })

  afterEach(() => {
    resetActiveRunsForTests()
    sinon.restore()
  })

  describe('drain and stop', () => {
    it('refuses new runs once the drain begins, with a 503 that asks to send again in a second', async () => {
      expect(() => refuseRunWhileShuttingDown()).to.not.throw()
      await drainRuns(1_000)
      expect(isShuttingDown()).to.equal(true)
      const error = (() => {
        try {
          refuseRunWhileShuttingDown()
        } catch (e) {
          return e
        }
        return undefined
      })() as ServiceUnavailableError
      expect(error).to.be.instanceOf(ServiceUnavailableError)
      expect(error.statusCode).to.equal(503)
      expect(error.metadata).to.deep.equal({ retryAfter: '1' })
    })

    it('waits for the runs going when it begins, and no longer once the last one ends', async () => {
      const ends = [trackRun(() => Promise.resolve()), trackRun(() => Promise.resolve())]
      let drained = false
      const drain = drainRuns(60_000).then(() => {
        drained = true
      })
      ends[0]!()
      await settle()
      expect(drained).to.equal(false)
      ends[1]!()
      await drain
      expect(drained).to.equal(true)
    })

    it('gives up waiting after the drain time', async () => {
      const clock = sinon.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
      trackRun(() => Promise.resolve())
      let drained = false
      void drainRuns(8_000).then(() => {
        drained = true
      })
      await clock.tickAsync(7_999)
      expect(drained).to.equal(false)
      await clock.tickAsync(1)
      expect(drained).to.equal(true)
    })

    it('stops every run still going, waits for them, and says how many there were', async () => {
      const stopped: string[] = []
      trackRun(async () => {
        await settle()
        stopped.push('a')
      })
      trackRun(() => Promise.reject(new Error('save failed')))
      expect(await stopAllRuns(5_000)).to.equal(2)
      expect(stopped).to.deep.equal(['a'])
      expect(await stopAllRuns(5_000), 'nothing was ended, so both are still tracked').to.equal(2)
    })

    it('waits at most the stop time for a run whose stop hangs', async () => {
      const clock = sinon.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
      trackRun(() => new Promise<void>(() => undefined))
      let done = false
      void stopAllRuns(5_000).then(() => {
        done = true
      })
      await clock.tickAsync(4_999)
      expect(done).to.equal(false)
      await clock.tickAsync(1)
      expect(done).to.equal(true)
    })

    it('tells each stop that the process is shutting down', async () => {
      const stop = sinon.stub().resolves()
      trackRun(stop, 'run-1')
      await stopAllRuns(5_000)
      expect(stop.firstCall.args).to.deep.equal(['shutdown'])
    })
  })

  describe('stop one run', () => {
    it('stops only the run with that id, as a stop, and waits for its save', async () => {
      let saved = false
      const stop = sinon.stub().callsFake(async () => {
        await settle()
        saved = true
      })
      const other = sinon.stub().resolves()
      trackRun(stop, 'run-1')
      trackRun(other, 'run-2')

      expect(await stopRun('run-1')).to.equal(true)
      expect(saved).to.equal(true)
      expect(stop.firstCall.args).to.deep.equal(['stop'])
      expect(other.called).to.equal(false)
    })

    it('is false for a run this process is not streaming, or one that has ended', async () => {
      const stop = sinon.stub().resolves()
      const ended = trackRun(stop, 'run-1')
      trackRun(sinon.stub().resolves())

      expect(await stopRun('run-2')).to.equal(false)
      ended()
      expect(await stopRun('run-1')).to.equal(false)
      expect(stop.called).to.equal(false)
    })
  })

  for (const flow of flows) {
    describe(flow.name, () => {
      it('is refused with a 503 before anything is stored once the server is stopping', async () => {
        const store = new InMemoryChatStore()
        const ai = new FakeAIBackend()
        store.install()
        ai.install()
        const prepared = flow.prepare(store)
        const res = new FakeSSEResponse()
        await drainRuns(0)
        const next = sinon.spy()
        await flow.handler(controllerModule)(requestFor(prepared) as never, res as never, next as never)
        expect(next.firstCall.args[0]).to.be.instanceOf(ServiceUnavailableError)
        expect(ai.streamCalls).to.deep.equal([])
        expect(store.writes).to.deep.equal([])
        expect(res.headersSent).to.equal(false)
      })

      it('a stop ends the stream with the restart frame, keeps what streamed and cancels the AI service', async () => {
        const run = await startStream(flow)
        run.ai.send('TEXT_MESSAGE_CONTENT', delta('So far, '))
        await settle()

        expect(await stopAllRuns(5_000)).to.equal(1)
        await run.res.ended

        expect(run.res.eventsOf('RUN_ERROR').map((e) => [e.data.code, e.data.message])).to.deep.equal([
          ['abort', 'PipesHub is restarting. Your answer so far was saved.'],
        ])
        expect(run.answer()).to.include({ content: 'So far, ', status: 'stopped' })
        expect(run.conversation().status).to.equal('Stopped')
        expect(streamSignal()?.aborted).to.equal(true)
        expect(await stopAllRuns(5_000), 'an ended run is no longer tracked').to.equal(0)
        expect(run.res.writesAfterEnd).to.equal(0)
      })

      it('a run that ends during the drain is saved as usual and ends the drain', async () => {
        const run = await startStream(flow)
        let drained = false
        const drain = drainRuns(60_000).then(() => {
          drained = true
        })
        await settle()
        expect(drained).to.equal(false)

        run.ai.send('RUN_FINISHED', finalAnswer('All of it.'))
        run.ai.finish()
        await run.res.ended
        await drain

        expect(drained).to.equal(true)
        expect(run.answer()?.content).to.equal('All of it.')
        expect(run.res.eventsOf('RUN_ERROR')).to.deep.equal([])
      })
    })
  }

  it('a non-streaming turn is refused with a 503 once the server is stopping', async () => {
    await drainRuns(0)
    const next = sinon.spy()
    const res = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis(), setHeader: sinon.stub() }
    await createConversation(appConfig, turnDeps())(
      { body: { query: 'Hello' }, params: {}, headers: {}, context: { requestId: 'r1' } } as never,
      res as never,
      next,
    )
    expect(next.firstCall.args[0]).to.be.instanceOf(ServiceUnavailableError)
    expect(res.json.called).to.equal(false)
  })
})
