/**
 * One `chat_stream.end` per client stream, a mid-stream error logged once with a class, no content.
 * Drives every streaming handler through the repo's harness (in-memory Mongo, FakeAIBackend on `fetch`, FakeSSEResponse).
 * Deterministic: no wall clock in assertions (durations are only checked to be integers), fake timers for the 30-minute cap.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Logger } from '../../../../src/libs/services/logger.service'
import { getRequestContext, runWithRequestContext } from '../../../../src/libs/context/request-context'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { InternalServerError } from '../../../../src/libs/errors/http.errors'
import { DETACHED_RUN_MAX_MS } from '../../../../src/modules/enterprise_search/utils/stream-lifecycle'
import { oid, settle } from './chat-test-harness'
import { AGENT_KEY, RUN, delta, finalAnswer, flows, startStream, Flow } from './streaming-flows'

const ANSWER = 'nightjar answer text'
const ERROR_TEXT = 'nightjar provider said no'

interface Rec { level: string; message: string; meta: Record<string, unknown> }
let recs: Rec[]

const ends = (): Rec[] => recs.filter((r) => r.message === 'chat_stream.end')
const onlyEnd = (): Rec => {
  expect(ends(), 'exactly one chat_stream.end').to.have.length(1)
  return ends()[0] as Rec
}
// Another file's app can leave the outbox dispatcher running in this process; its failures are not this stream's.
const BACKGROUND_LINES = new Set(['Outbox dispatcher pass failed'])
const atLeastWarn = (): Rec[] =>
  recs.filter((r) => (r.level === 'warn' || r.level === 'error') && !BACKGROUND_LINES.has(r.message))

const finish = async (run: Awaited<ReturnType<typeof startStream>>): Promise<void> => {
  run.ai.finish()
  await Promise.race([run.res.ended, settle(40)])
  await settle(10)
}

describe('N3: chat stream end line', () => {
  beforeEach(() => {
    recs = []
    for (const level of ['error', 'warn', 'info', 'debug'] as const) {
      sinon.stub(Logger.prototype, level).callsFake(function (message: string, meta?: unknown) {
        recs.push({ level, message, meta: (meta ?? {}) as Record<string, unknown> })
      } as never)
    }
  })
  afterEach(() => sinon.restore())

  for (const flow of flows) {
    describe(flow.name, () => {
      it('a completed stream writes one INFO end line with its counts and no content', async () => {
        const run = await startStream(flow)
        run.ai.send('TEXT_MESSAGE_CONTENT', delta(ANSWER))
        run.ai.send('RUN_FINISHED', finalAnswer(ANSWER))
        await finish(run)

        const end = onlyEnd()
        expect(end.level).to.equal('info')
        expect(end.meta).to.include({ outcome: 'success', reason: 'completed', event_name: 'chat_stream.end' })
        expect(end.meta.events, 'frames received from the AI service').to.equal(2)
        expect(end.meta.bytes).to.be.a('number').and.greaterThan(0)
        expect(Number.isInteger(end.meta.duration_ms)).to.equal(true)
        expect(Number.isInteger(end.meta.ttfb_ms)).to.equal(true)
        expect(atLeastWarn()).to.deep.equal([])
        expect(JSON.stringify(recs)).to.not.contain('nightjar')
      })

      it("logs Python's RUN_ERROR once, as a WARN end line with its code", async () => {
        const run = await startStream(flow)
        run.ai.send('TEXT_MESSAGE_CONTENT', delta('Half'))
        run.ai.send('RUN_ERROR', { runId: RUN, message: ERROR_TEXT, code: 'llm_provider_error' })
        await finish(run)

        const end = onlyEnd()
        expect(end.level).to.equal('warn')
        expect(end.meta).to.include({ outcome: 'failure', reason: 'upstream_error', error_class: 'upstream_5xx', code: 'llm_provider_error' })
        expect(atLeastWarn(), 'the end line is the only report').to.deep.equal([end])
        expect(JSON.stringify(recs)).to.not.contain('nightjar')
        expect(run.conversation().failReason, 'out of the logs, still saved').to.equal(ERROR_TEXT)
      })

      it('reports a graceful error answer (a classified-failure RUN_FINISHED) as an upstream error', async () => {
        const run = await startStream(flow)
        run.ai.send('RUN_FINISHED', { runId: RUN, result: { answer: ERROR_TEXT, citations: [], answerMatchType: 'Error', errorCode: 'llm_provider_error' } })
        await finish(run)

        const end = onlyEnd()
        expect(end.level).to.equal('warn')
        expect(end.meta).to.include({ outcome: 'failure', reason: 'upstream_error', code: 'llm_provider_error' })
        expect(JSON.stringify(recs)).to.not.contain('nightjar')
      })

      it('reports a Stop as cancelled, at INFO', async () => {
        const run = await startStream(flow)
        run.ai.send('RUN_FINISHED', { runId: RUN, result: { answer: 'Part', citations: [], status: 'stopped' } })
        await finish(run)

        const end = onlyEnd()
        expect(end.level).to.equal('info')
        expect(end.meta).to.include({ outcome: 'partial', reason: 'cancelled' })
      })

      it('a cut midway is reported by the call line; the end line says the same class at INFO, and nothing else repeats it', async () => {
        const run = await startStream(flow)
        run.ai.send('TEXT_MESSAGE_CONTENT', delta('Half'))
        await settle()
        run.ai.breakConnection(Object.assign(new TypeError('terminated'), { cause: { code: 'UND_ERR_SOCKET' } }))
        await Promise.race([run.res.ended, settle(40)])
        await settle(10)

        const call = recs.find((r) => r.message === 'upstream.call.end')
        const end = onlyEnd()
        expect(end.level).to.equal('info')
        expect(end.meta).to.include({ outcome: 'failure', reason: 'upstream_error', error_class: call?.meta.error_class })
        expect(atLeastWarn().map((r) => r.message), 'one report: N2 call line').to.deep.equal(['upstream.call.end'])
      })

      it('a refusal before the first byte (500) has one report (the call line) and an end line with no frames', async () => {
        await startStream(flow, undefined, (ai) => ai.refuseStream(500, { detail: ERROR_TEXT }))
        await settle(10)

        const end = onlyEnd()
        expect(end.level).to.equal('info')
        expect(end.meta).to.include({ outcome: 'failure', reason: 'upstream_error', error_class: 'upstream_5xx', events: 0, bytes: 0 })
        expect(atLeastWarn().map((r) => r.message)).to.deep.equal(['upstream.call.end'])
        expect(JSON.stringify(recs)).to.not.contain('nightjar')
      })

      it('a browser that leaves before the end: one end line when the detached run finishes, reason client_disconnect', async () => {
        const run = await startStream(flow)
        run.ai.send('TEXT_MESSAGE_CONTENT', delta('Half'))
        await settle()
        run.res.disconnect()
        await settle(10)
        expect(ends(), 'nothing yet: the run is still going').to.deep.equal([])
        run.ai.send('RUN_FINISHED', finalAnswer(ANSWER))
        await finish(run)

        const end = onlyEnd()
        expect(end.level).to.equal('info')
        expect(end.meta).to.include({ outcome: 'success', reason: 'client_disconnect' })
      })

      it('a detached run abandoned at the cap: one WARN end line, partial', async () => {
        const clock = sinon.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
        const run = await startStream(flow)
        run.ai.send('TEXT_MESSAGE_CONTENT', delta('Half'))
        await settle()
        run.res.disconnect()
        await settle(10)
        clock.tick(DETACHED_RUN_MAX_MS + 1)
        await settle(20)

        const end = onlyEnd()
        expect(end.level).to.equal('warn')
        expect(end.meta).to.include({ outcome: 'partial', reason: 'client_disconnect', code: 'detached_cap' })
      })

      it('a save that fails is our fault: one ERROR end line with the error, and the handler adds no line of its own', async () => {
        const run = await startStream(flow)
        const write = (flow.regenerate ? ChatSessionMessage.findOneAndReplace : ChatSessionMessage.insertMany) as unknown as sinon.SinonStub
        write.onCall(write.callCount).rejects(new Error('E11000 duplicate key error collection: es.chatSessionMessages'))
        run.ai.send('RUN_FINISHED', finalAnswer(ANSWER))
        await finish(run)

        const end = onlyEnd()
        expect(end.level).to.equal('error')
        expect(end.meta).to.include({ outcome: 'failure', reason: 'internal_error', error_class: 'internal' })
        expect(end.meta.error).to.be.instanceOf(Error)
        const handlerLines = recs.filter((r) => /^Failed to save (complete conversation|AI response|regenerated)/.test(r.message))
        expect(handlerLines).to.deep.equal([])
      })

      it('a malformed final frame is logged without its data', async () => {
        const run = await startStream(flow)
        run.ai.sendRaw(`event: RUN_FINISHED\ndata: {"result": {"answer": "${ANSWER}"\n\n`)
        await finish(run)

        expect(recs.filter((r) => r.message === 'chat_stream.bad_frame').map((r) => [r.level, r.meta.frame])).to.deep.equal([
          ['warn', 'RUN_FINISHED'],
        ])
        expect(JSON.stringify(recs)).to.not.contain('nightjar')
        expect(recs.some((r) => 'dataLine' in r.meta || 'answer' in r.meta || 'failReason' in r.meta)).to.equal(false)
        const end = onlyEnd()
        expect(end.level).to.equal('warn')
        expect(end.meta).to.include({ outcome: 'failure', reason: 'upstream_error', code: 'no_response' })
      })

      it('a stream that ends with no final answer and no error: one WARN end line, code no_response', async () => {
        const run = await startStream(flow)
        run.ai.send('TEXT_MESSAGE_CONTENT', delta('Half'))
        await finish(run)

        const end = onlyEnd()
        expect(end.level).to.equal('warn')
        expect(end.meta).to.include({ outcome: 'failure', reason: 'upstream_error', error_class: 'upstream_5xx', code: 'no_response' })
      })

      it('the cap firing while the open is still pending: exactly one abandoned end line', async () => {
        const clock = sinon.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
        const run = await startStream(flow, undefined, (ai) => ai.stallOpen(), { wait: false })
        run.res.disconnect()
        await settle(10)
        clock.tick(DETACHED_RUN_MAX_MS + 1)
        await settle(20)
        await run.done

        const end = onlyEnd()
        expect(end.level).to.equal('warn')
        expect(end.meta).to.include({ outcome: 'partial', reason: 'client_disconnect', code: 'detached_cap' })
      })

      it('the cap firing after the final answer arrived (stream never closed): one INFO end line, client_disconnect success', async () => {
        const clock = sinon.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
        const run = await startStream(flow)
        run.ai.send('RUN_FINISHED', finalAnswer(ANSWER))
        await settle()
        run.res.disconnect()
        await settle(10)
        clock.tick(DETACHED_RUN_MAX_MS + 1)
        await settle(20)

        const end = onlyEnd()
        expect(end.level).to.equal('info')
        expect(end.meta).to.include({ outcome: 'success', reason: 'client_disconnect' })
      })

      it('a 402 refusal at the open is reported by the call line; the end line is INFO bad_request', async () => {
        const run = await startStream(flow, undefined, (ai) =>
          ai.refuseStream(402, { detail: { code: 'quota_exceeded', message: ERROR_TEXT } }),
        )
        await settle(10)

        const end = onlyEnd()
        expect(end.level).to.equal('info')
        expect(end.meta).to.include({ outcome: 'failure', reason: 'upstream_error', error_class: 'bad_request', events: 0 })
        expect(atLeastWarn().map((r) => r.message)).to.deep.equal(['upstream.call.end'])
        expect(JSON.stringify(recs)).to.not.contain('nightjar')
        expect(run.res.writableEnded).to.equal(true)
      })

      it('our own failure at the open (no call line was written) is ERROR internal_error with the error', async () => {
        sinon.stub(AIServiceCommand.prototype, 'executeStream').rejects(new InternalServerError('Blocked URL'))
        await startStream(flow)
        await settle(10)

        const end = onlyEnd()
        expect(end.level).to.equal('error')
        expect(end.meta).to.include({ outcome: 'failure', reason: 'internal_error', error_class: 'internal' })
        expect(end.meta.error).to.be.instanceOf(Error)
        expect(recs.some((r) => r.message === 'upstream.call.end')).to.equal(false)
      })

      if (flow.regenerate) {
        it('our own deliberate 4xx inside the stream (a message id that does not exist): one WARN rejected line, no stack', async () => {
          const missing: Flow = {
            ...flow,
            prepare: (store) => {
              const prepared = flow.prepare(store)
              return { ...prepared, params: { ...prepared.params, messageId: String(oid()) } }
            },
          }
          const run = await startStream(missing)
          await settle(10)

          const end = onlyEnd()
          expect(end.level).to.equal('warn')
          expect(end.meta).to.include({ outcome: 'failure', reason: 'rejected', error_class: 'bad_request', error_type: 'BadRequestError' })
          expect(end.meta).to.not.have.property('error')
          expect(atLeastWarn()).to.deep.equal([end])
          expect(run.res.writableEnded).to.equal(true)
        })
      }

      it('binds conversation_id (and agent_id) once, so every later line of the request carries them', async () => {
        const store = { rootId: 'rq-1', logFields: {} } as never
        let run: Awaited<ReturnType<typeof startStream>> | undefined
        await runWithRequestContext(store, async () => {
          run = await startStream(flow)
          run.ai.send('RUN_FINISHED', finalAnswer('ok'))
          await finish(run)
        })
        const bound = (store as { logFields: Record<string, unknown> }).logFields
        expect(bound.conversation_id).to.equal(String(run?.conversation()._id))
        if (flow.agent) expect(bound.agent_id).to.equal(AGENT_KEY)
      })
    })
  }

  it('the browser-left line runs in the request context and at DEBUG (the end line reports it)', async () => {
    const flow = flows[0] as Flow
    const store = { rootId: 'rq-close', logFields: {} } as never
    let seen: unknown
    let run: Awaited<ReturnType<typeof startStream>> | undefined
    await runWithRequestContext(store, async () => {
      run = await startStream(flow)
    })
    ;(Logger.prototype.info as sinon.SinonStub).callsFake((message: string) => {
      if (message.startsWith('Client disconnected')) seen = getRequestContext()
    })
    ;(Logger.prototype.debug as sinon.SinonStub).callsFake((message: string) => {
      if (message.startsWith('Client disconnected') || message === 'chat_stream.client_left') seen = getRequestContext()
      recs.push({ level: 'debug', message, meta: {} })
    })
    run?.res.disconnect() // emitted outside any context, as a socket close is
    await settle(5)
    expect(seen, 'logged inside the request context').to.equal(store)
    expect(recs.some((r) => r.level === 'debug' && /Client disconnected|client_left/.test(r.message))).to.equal(true)
  })
})
