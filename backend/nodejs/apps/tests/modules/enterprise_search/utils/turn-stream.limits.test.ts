import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { conversationExtensions } from '../../../../src/modules/enterprise_search/services/conversation/conversation-extensions'
import { CHAT_ERROR_MESSAGES } from '../../../../src/modules/enterprise_search/utils/chat-error-messages'
import { resetActiveRunsForTests, stopAllRuns } from '../../../../src/modules/enterprise_search/utils/active-runs'
import {
  DEFAULT_RUN_LIMITS,
  RunLimit,
  RunWatchdog,
} from '../../../../src/modules/enterprise_search/utils/turn-stream'
import { settle } from '../controller/chat-test-harness'
import { delta, finalAnswer, flows, startStream } from '../controller/streaming-flows'

const frame = (type: string, fields: Record<string, unknown> = {}): string =>
  `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`

const streamSignal = (): AbortSignal | undefined => {
  const call = (globalThis.fetch as unknown as sinon.SinonStub)
    .getCalls()
    .find((c) => /\/stream$/.test(String(c.args[0])))
  return (call?.args[1] as RequestInit | undefined)?.signal ?? undefined
}

describe('run limits', () => {
  let clock: sinon.SinonFakeTimers

  beforeEach(() => {
    resetActiveRunsForTests()
    clock = sinon.useFakeTimers({ now: Date.parse('2026-10-16T10:00:00Z'), toFake: ['setInterval', 'clearInterval', 'Date'] })
  })

  afterEach(() => {
    clock.restore()
    resetActiveRunsForTests()
    sinon.restore()
  })

  describe('RunWatchdog', () => {
    const watch = (limits = DEFAULT_RUN_LIMITS) => {
      const breaches: RunLimit[] = []
      const watchdog = new RunWatchdog(limits, (limit) => breaches.push(limit))
      return { watchdog, breaches }
    }
    const LEASED = { silenceMs: 60_000, idleMs: 300_000, toolIdleMs: 660_000, maxMs: 900_000 }

    it('ends a stream with no bytes for 60 s, checked every 5 s; any byte starts the minute again', () => {
      const { watchdog, breaches } = watch()
      clock.tick(55_000)
      watchdog.onChunk(frame('HEARTBEAT'))
      clock.tick(55_000)
      expect(breaches).to.deep.equal([])
      clock.tick(5_000)
      expect(breaches).to.deep.equal(['silence'])
      clock.tick(120_000)
      expect(breaches, 'once').to.deep.equal(['silence'])
    })

    it('without an idle limit, heartbeats keep a quiet stream alive', () => {
      const { watchdog, breaches } = watch()
      for (let i = 0; i < 60; i += 1) {
        clock.tick(15_000)
        watchdog.onChunk(frame('HEARTBEAT'))
      }
      expect(breaches).to.deep.equal([])
      watchdog.stop()
    })

    it('with an idle limit, heartbeats do not count as progress', () => {
      const { watchdog, breaches } = watch({ ...LEASED, maxMs: undefined })
      watchdog.onChunk(frame('TEXT_MESSAGE_CONTENT', delta('Thinking')))
      for (let i = 0; i < 19; i += 1) {
        clock.tick(15_000)
        watchdog.onChunk(frame('HEARTBEAT'))
      }
      expect(breaches).to.deep.equal([])
      clock.tick(15_000)
      expect(breaches).to.deep.equal(['idle'])
    })

    it('a 10-minute tool call is not cut: an open call waits up to the tool limit, and its end restores the idle limit', () => {
      const { watchdog, breaches } = watch({ ...LEASED, maxMs: undefined })
      // A frame split across chunks is read once.
      const start = frame('TOOL_CALL_START', { toolCallId: 't1', toolCallName: 'mcp_search' })
      watchdog.onChunk(start.slice(0, 20))
      watchdog.onChunk(start.slice(20))
      for (let i = 0; i < 40; i += 1) {
        clock.tick(15_000)
        watchdog.onChunk(frame('HEARTBEAT'))
      }
      expect(breaches, '600 s with only heartbeats while the call is open').to.deep.equal([])
      watchdog.onChunk(frame('TOOL_CALL_END', { toolCallId: 't1' }) + frame('TOOL_CALL_RESULT', { toolCallId: 't1' }))
      for (let i = 0; i < 19; i += 1) {
        clock.tick(15_000)
        watchdog.onChunk(frame('HEARTBEAT'))
      }
      expect(breaches).to.deep.equal([])
      clock.tick(15_000)
      expect(breaches).to.deep.equal(['idle'])
    })

    it('a tool call that never ends still meets the tool limit', () => {
      const { watchdog, breaches } = watch({ ...LEASED, maxMs: undefined })
      watchdog.onChunk(frame('TOOL_CALL_START', { toolCallId: 't1' }))
      for (let i = 0; i < 44; i += 1) {
        clock.tick(15_000)
        watchdog.onChunk(frame('HEARTBEAT'))
      }
      expect(breaches).to.deep.equal(['idle'])
    })

    it('the maximum ends a run that keeps progressing', () => {
      const { watchdog, breaches } = watch(LEASED)
      for (let i = 0; i < 179; i += 1) {
        clock.tick(5_000)
        watchdog.onChunk(frame('TEXT_MESSAGE_CONTENT', delta('.')))
      }
      expect(breaches).to.deep.equal([])
      clock.tick(5_000)
      expect(breaches).to.deep.equal(['max'])
    })

    it('stops checking once stopped', () => {
      const { watchdog, breaches } = watch()
      watchdog.stop()
      clock.tick(600_000)
      expect(breaches).to.deep.equal([])
    })
  })

  for (const flow of flows) {
    describe(flow.name, () => {
      it('ends a run whose AI service fell silent: the browser gets a timeout, what streamed is kept, the AI service is cancelled', async () => {
        const run = await startStream(flow)
        run.ai.send('TEXT_MESSAGE_CONTENT', delta('Three '))
        await settle()

        clock.tick(55_000)
        await settle()
        expect(run.res.writableEnded).to.equal(false)
        clock.tick(5_000)
        await settle(20)

        expect(run.res.eventsOf('RUN_ERROR').map((e) => [e.data.code, e.data.message])).to.deep.equal([
          ['timeout', CHAT_ERROR_MESSAGES.timedOut],
        ])
        expect(run.res.writableEnded).to.equal(true)
        expect(run.answer()).to.include({ content: 'Three ', status: 'stopped' })
        expect(run.conversation().status).to.equal('Stopped')
        expect(streamSignal()?.aborted).to.equal(true)
        expect(await stopAllRuns(1_000), 'the ended run is no longer tracked').to.equal(0)
      })

      it('records an error when a run that timed out had no text', async () => {
        const run = await startStream(flow)
        clock.tick(60_000)
        await settle(20)

        expect(run.res.eventsOf('RUN_ERROR').map((e) => e.data.code)).to.deep.equal(['timeout'])
        expect(run.answer()).to.include({ messageType: 'error', content: CHAT_ERROR_MESSAGES.timedOut })
        expect(run.conversation().status).to.equal('Failed')
      })

      it('keeps a run going on heartbeats alone without a lease, and saves it when it finishes', async () => {
        const run = await startStream(flow)
        for (let i = 0; i < 40; i += 1) {
          clock.tick(15_000)
          run.ai.send('HEARTBEAT')
          await settle()
        }
        run.ai.send('RUN_FINISHED', finalAnswer('Ten minutes later.'))
        run.ai.finish()
        await run.res.ended

        expect(run.res.eventsOf('RUN_ERROR')).to.deep.equal([])
        expect(run.answer()?.content).to.equal('Ten minutes later.')
      })

      it('ends the run on the limits its turn extension sets', async () => {
        const runLimits = sinon.stub().returns({ silenceMs: 60_000, idleMs: 30_000 })
        sinon.stub(conversationExtensions().turns, 'streamFor').returns({
          respondingAgentView: () => Promise.resolve(undefined),
          runLimits,
        })
        const run = await startStream(flow)
        run.ai.send('TEXT_MESSAGE_CONTENT', delta('Partly'))
        await settle()
        clock.tick(15_000)
        run.ai.send('HEARTBEAT')
        await settle()
        expect(run.res.writableEnded).to.equal(false)
        clock.tick(15_000)
        await settle(20)

        expect(runLimits.calledOnce).to.equal(true)
        expect(String(runLimits.firstCall.args[1]._id)).to.equal(String(run.conversation()._id))
        expect(run.res.eventsOf('RUN_ERROR').map((e) => e.data.code)).to.deep.equal(['timeout'])
        expect(run.answer()).to.include({ content: 'Partly', status: 'stopped' })
      })

      it('saves an answer that arrived before the AI service went silent', async () => {
        const run = await startStream(flow)
        run.ai.send('RUN_FINISHED', finalAnswer('Done, then silence.'))
        await settle()
        clock.tick(60_000)
        await settle(20)

        expect(run.res.eventsOf('RUN_ERROR')).to.deep.equal([])
        expect(run.res.eventsOf('RUN_FINISHED')).to.have.length(1)
        expect(run.answer()?.content).to.equal('Done, then silence.')
        expect(run.answer()?.status).to.not.equal('stopped')
      })
    })
  }

})
