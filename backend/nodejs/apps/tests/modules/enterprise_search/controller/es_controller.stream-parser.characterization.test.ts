/**
 * Behaviour baseline of how the streaming routes read the AI stream: what each one forwards, drops and saves for every
 * frame kind the AI service can send. A change to the stream reading must leave it passing.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { CHAT_ERROR_MESSAGES } from '../../../../src/modules/enterprise_search/utils/chat-error-messages'
import { DETACHED_RUN_MAX_MS } from '../../../../src/modules/enterprise_search/utils/stream-lifecycle'
import { settle } from './chat-test-harness'
import { RUN, appConfig, delta, finalAnswer, flows, startStream } from './streaming-flows'
import { registerUpstreams } from '../../../../src/libs/http/upstream-call'
import { registerAppUpstreams } from '../../../../src/modules/tokens_manager/config/config'

const frame = (type: string, fields: Record<string, unknown>): string =>
  `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`

const question = { toolData: { question: 'Which quarter?' } }

// What the AI service sends, in this order, as several network chunks below.
const sent = [
  frame('RUN_STARTED', { runId: RUN, threadId: 't1' }),
  frame('TOOL_CALL_START', { toolCallId: 'tc1', toolCallName: 'search', parentMessageId: 'm1' }),
  frame('TEXT_MESSAGE_CONTENT', delta('Hello ')),
  'event: TEXT_MESSAGE_CONTENT\ndata: {not json\n\n',
  frame('CUSTOM', { name: 'ask_user_question', value: question }),
  frame('CUSTOM', { name: 'retrieval_context', value: { records: 1 } }),
  'event: CUSTOM\ndata: {not json\n\n',
  frame('RUN_FINISHED', { runId: 'run-child', parentRunId: RUN, result: { answer: 'child answer', citations: [] } }),
  frame('RUN_ERROR', { runId: 'run-child', parentRunId: RUN, message: 'Sub-agent failed', code: 'agent_error' }),
  // Legacy-protocol event names: no producer sends them.
  'event: complete\ndata: {"answer":"legacy"}\n\n',
  'event: answer_chunk\ndata: {"accumulated":"legacy"}\n\n',
  'event: error\ndata: {"error":"legacy"}\n\n',
  'data: {"no":"event line"}\n\n',
  'event: STATE_SNAPSHOT\ndata:{"type":"STATE_SNAPSHOT",\ndata: "snapshot":{}}\n\n',
]
// What the browser gets of them, in order: only the event types, names and fields the relay knows, each written again
// on one `data:` line. An unreadable frame, a legacy event name and a frame with no event line add no byte, and a
// sub-agent's RUN_FINISHED keeps only its run ids: its result stays on the server.
const forwarded = [
  sent[0],
  sent[1],
  sent[2],
  sent[4],
  sent[5],
  frame('RUN_FINISHED', { runId: 'run-child', parentRunId: RUN }),
  sent[8],
  frame('STATE_SNAPSHOT', { snapshot: {} }),
] as string[]
// Dropped: not forwarded (an unreadable run frame, and the root answer Node re-sends itself).
const unreadableFinish = 'event: RUN_FINISHED\ndata: \n\n'
const rootAnswer = frame('RUN_FINISHED', finalAnswer('Done.'))

describe('chat stream parser behaviour baseline', () => {
  beforeEach(() => registerAppUpstreams(appConfig))
  afterEach(() => registerUpstreams({}))
  afterEach(() => sinon.restore())

  for (const flow of flows) {
    describe(flow.name, () => {
      it('forwards what the relay knows, drops the rest, and captures and saves every frame kind', async () => {
        const run = await startStream(flow)
        const messagesBefore = run.messages().length

        run.ai.sendRaw(sent.slice(0, 2).join(''))
        // Several frames in one chunk, then one frame split across two chunks.
        run.ai.sendRaw(sent.slice(2, 7).join(''))
        const split = sent[7] as string
        run.ai.sendRaw(split.slice(0, 30))
        await settle()
        run.ai.sendRaw(split.slice(30) + unreadableFinish + (sent[8] as string))
        for (const raw of sent.slice(9)) run.ai.sendRaw(raw)
        // An empty and a whitespace-only frame: both are skipped, not forwarded.
        run.ai.sendRaw('\n\n \n\n')
        run.ai.sendRaw(rootAnswer)
        run.ai.finish()
        await run.res.ended
        await settle()

        // Exactly the forwarded frames sit between Node's opening frames and its own RUN_FINISHED:
        // the dropped frames, the unreadable RUN_FINISHED, the blank frames and the root answer add no byte.
        const start = run.res.body.indexOf(forwarded[0] as string)
        const end = run.res.body.lastIndexOf('event: RUN_FINISHED')
        expect(start, 'the first forwarded frame is present').to.be.greaterThan(-1)
        expect(run.res.body.slice(start, end), 'every forwarded frame, in order').to.equal(forwarded.join(''))
        expect(run.res.body).to.not.contain('\n\n\n\n')
        expect(run.res.body).to.not.contain('event: RUN_FINISHED\ndata: \n\n')
        expect(run.res.body).to.not.contain('"answer":"Done."')
        expect(run.res.body, 'a sub-agent’s result').to.not.contain('child answer')
        const finished = run.res.eventsOf('RUN_FINISHED')
        expect(finished, 'the child frame, then Node’s own').to.have.length(2)
        expect(finished[1]?.data.result).to.have.property('conversation')
        expect(run.res.eventsOf('RUN_ERROR').map((e) => e.data.runId)).to.deep.equal(['run-child'])
        expect(run.res.writesAfterEnd).to.equal(0)

        const conversation = run.conversation()
        expect(conversation.status).to.equal('Complete')
        expect(conversation.conversationErrors ?? []).to.have.length(0)
        expect(run.answer()?.content).to.equal('Done.')

        const toolCalls = run.messages().filter((m) => m.messageType === 'tool_call')
        if (flow.regenerate) {
          // Regenerate replaces the answer in place and attaches the question to it.
          expect(toolCalls).to.have.length(0)
          expect(run.messages()).to.have.length(messagesBefore)
          expect(JSON.stringify(run.answer())).to.contain('Which quarter?')
        } else {
          // The other four save the question at once as its own tool_call message.
          expect(toolCalls).to.have.length(1)
          const tools = toolCalls[0]?.tools as Array<{ toolName: string; toolResult: unknown }>
          expect(tools.map((t) => [t.toolName, t.toolResult])).to.deep.equal([['ask_user_question', question.toolData]])
          // The tool_call and the answer; the question is already saved before the stream opens.
          expect(run.messages()).to.have.length(messagesBefore + 2)
        }
      })

      it('forwards a root RUN_ERROR once, as the relay writes it, and saves its reason and code the way this route does', async () => {
        const run = await startStream(flow)
        const reason = 'This conversation is too long for the selected model.'

        run.ai.send('TEXT_MESSAGE_CONTENT', delta('Partial'))
        run.ai.sendRaw(frame('RUN_ERROR', { runId: RUN, message: reason, code: 'context_length_exceeded', stack: 'Trace: here' }))
        run.ai.finish()
        await run.res.ended
        await settle()

        // Its message and code only: Python's stack never reaches the browser.
        expect(run.res.body).to.contain(frame('RUN_ERROR', { runId: RUN, message: reason, code: 'context_length_exceeded' }))
        expect(run.res.body).to.not.contain('Trace: here')
        expect(run.res.eventsOf('RUN_ERROR'), 'forwarded, and Node adds none').to.have.length(1)
        const conversation = run.conversation()
        expect(conversation.status).to.equal('Failed')
        expect(conversation.failReason).to.equal(reason)
        const errors = (conversation.conversationErrors ?? []) as Array<{ errorType?: string; stack?: string }>
        // Regenerate stores a fixed code; the other four store the frame's code. Every load reads the session, so no
        // route keeps the stack on it.
        const code = flow.regenerate ? 'streaming_error' : 'context_length_exceeded'
        expect(errors.map((e) => [e.errorType, e.stack])).to.deep.equal([[code, undefined]])
        expect(run.answer()?.content).to.equal(reason)
      })

      it('a RUN_ERROR without a message saves the generic reason', async () => {
        const run = await startStream(flow)

        run.ai.send('RUN_ERROR', { runId: RUN, code: 7 })
        run.ai.finish()
        await run.res.ended
        await settle()

        const conversation = run.conversation()
        expect(conversation.status).to.equal('Failed')
        expect(conversation.failReason).to.equal(CHAT_ERROR_MESSAGES.failed)
        expect(run.answer()?.content).to.equal(CHAT_ERROR_MESSAGES.failed)
        const errors = (conversation.conversationErrors ?? []) as Array<{ errorType?: string; stack?: string }>
        expect(errors.map((e) => [e.errorType, e.stack])).to.deep.equal([['streaming_error', undefined]])
        expect(run.res.eventsOf('RUN_ERROR')).to.have.length(1)
      })

      it('keeps an approved TOOL_CALL_START on the partial answer saved when the run is abandoned, beside its other steps', async () => {
        const clock = sinon.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
        const run = await startStream(flow)

        run.ai.send('TOOL_CALL_START', { toolCallId: 'approved_1', toolCallName: 'mcp_jira_create_issue', approved: true })
        run.ai.send('TOOL_CALL_START', { toolCallId: 'call-2', toolCallName: 'search' })
        run.ai.send('TEXT_MESSAGE_CONTENT', delta('Created the issue'))
        await settle()
        run.res.disconnect()
        await settle(10)
        clock.tick(DETACHED_RUN_MAX_MS + 1)
        await settle(20)

        expect(run.conversation().status).to.equal('Stopped')
        const saved = run.answer()
        expect(saved?.content).to.equal('Created the issue')
        const parts = JSON.parse(JSON.stringify(saved?.parts ?? [])) as Array<Record<string, unknown>>
        // A stopped answer keeps its steps as a finished one does: both calls, cut short before their results. The
        // approved one says so, so the reply is never regenerated.
        expect(parts.filter((p) => p.type === 'tool_call').map((p) => [p.toolCallId, p.toolName, p.approved, p.status])).to.deep.equal([
          ['approved_1', 'mcp_jira_create_issue', true, 'blocked'],
          ['call-2', 'search', undefined, 'blocked'],
        ])
      })
    })
  }
})
