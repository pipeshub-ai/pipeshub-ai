import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controllerModule from '../../../../src/modules/enterprise_search/controller/es_controller'
import { CHAT_ERROR_MESSAGES } from '../../../../src/modules/enterprise_search/utils/chat-error-messages'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, settle } from './chat-test-harness'
import { Flow, ORG, OWNER, RUN, appConfig, delta, finalAnswer, flows, seedConversation, startStream } from './streaming-flows'

describe('es_controller streaming answers', () => {
  afterEach(() => {
    sinon.restore()
  })

  for (const flow of flows) {
    describe(flow.name, () => {
      it('streams the answer to the browser as it arrives and saves it when the AI service finishes', async () => {
        const run = await startStream(flow)
        const messagesBefore = run.prepared.existing ? 2 : 0

        run.ai.send('TEXT_MESSAGE_CONTENT', delta('Three '))
        // One frame split across two network chunks must still be read as one frame.
        const frame = `event: TEXT_MESSAGE_CONTENT\ndata: ${JSON.stringify({ type: 'TEXT_MESSAGE_CONTENT', ...delta('themes.') })}\n\n`
        run.ai.sendRaw(frame.slice(0, 25))
        await settle()
        run.ai.sendRaw(frame.slice(25))
        run.ai.send('RUN_FINISHED', finalAnswer('Three themes.'))
        run.ai.finish()
        await run.res.ended

        expect(run.ai.streamCalls).to.have.length(1)
        expect(run.ai.streamCalls[0]?.url).to.equal(`http://ai.test${flow.aiPath}`)
        expect(run.res.eventsOf('TEXT_MESSAGE_CONTENT').map((e) => e.data.delta)).to.deep.equal(['Three ', 'themes.'])
        const finished = run.res.eventsOf('RUN_FINISHED')
        expect(finished, 'only Node’s own RUN_FINISHED reaches the browser').to.have.length(1)
        expect(finished[0]?.data.result).to.have.property('conversation')
        expect(run.res.eventsOf('RUN_ERROR')).to.deep.equal([])
        expect(run.res.writesAfterEnd).to.equal(0)

        expect(run.answer()?.content).to.equal('Three themes.')
        expect(run.conversation().status).to.not.equal('Failed')
        expect(run.messages()).to.have.length(flow.regenerate ? messagesBefore : messagesBefore + 2)
        if (!flow.regenerate) expect(run.conversation().status).to.equal('Complete')
      })

      it('shows an error the AI service reports mid-answer exactly once and keeps its reason on the conversation', async () => {
        const run = await startStream(flow)
        const reason = 'This conversation is too long for the selected model. Start a new chat to continue.'

        run.ai.send('TEXT_MESSAGE_CONTENT', delta('Partial'))
        run.ai.send('RUN_ERROR', { runId: RUN, message: reason, code: 'context_length_exceeded' })
        run.ai.finish()
        await run.res.ended
        await settle()

        const errors = run.res.eventsOf('RUN_ERROR')
        expect(errors.map((e) => e.data.message), 'RUN_ERROR frames sent to the browser').to.deep.equal([reason])
        expect(run.conversation().status).to.equal('Failed')
        expect(run.conversation().failReason).to.equal(reason)
        expect(run.answer()?.content).to.equal(reason)
      })

      it('keeps what the user already saw when they close the tab mid-answer, and stops the AI service', async () => {
        const run = await startStream(flow)

        run.ai.send('TEXT_MESSAGE_CONTENT', delta('The roadmap '))
        run.ai.send('TEXT_MESSAGE_CONTENT', delta('[sub-agent notes]', { runId: 'run-child', parentRunId: RUN }))
        run.ai.send('TEXT_MESSAGE_CONTENT', delta('has three themes'))
        await settle()
        const bytesBeforeDisconnect = run.res.body.length
        run.res.disconnect()
        await settle(10)

        expect(run.conversation().status).to.equal('Stopped')
        const saved = run.answer()
        expect(saved?.content, 'partial answer, without sub-agent text').to.equal('The roadmap has three themes')
        expect(saved?.status).to.equal('stopped')
        expect(run.res.body.length, 'nothing is written to a closed connection').to.equal(bytesBeforeDisconnect)
        expect(run.res.eventsOf('RUN_ERROR')).to.deep.equal([])
      })

      it('tells the user the answer was interrupted when the connection to the AI service breaks mid-answer', async () => {
        const run = await startStream(flow)

        run.ai.send('TEXT_MESSAGE_CONTENT', delta('Half an ans'))
        await settle()
        run.ai.breakConnection(Object.assign(new TypeError('terminated'), { cause: { code: 'UND_ERR_SOCKET' } }))
        await run.res.ended
        await settle()

        const errors = run.res.eventsOf('RUN_ERROR')
        expect(errors).to.have.length(1)
        expect(errors[0]?.data.message).to.equal(CHAT_ERROR_MESSAGES.interrupted)
        expect(JSON.stringify(errors[0]?.data)).to.not.match(/UND_ERR_SOCKET|terminated/)
        expect(run.conversation().status).to.equal('Failed')
        expect(run.conversation().failReason).to.equal(CHAT_ERROR_MESSAGES.interrupted)
      })

      it('gives a plain "try again" message when the AI service refuses to start the answer', async () => {
        const run = await startStream(flow, controllerModule, (ai) => {
          ai.refuseStream(503, { detail: 'upstream qdrant-0.svc.cluster.local timed out' })
        })
        await run.res.ended
        await settle()

        const errors = run.res.eventsOf('RUN_ERROR')
        expect(errors).to.have.length(1)
        expect(errors[0]?.data.message).to.equal(CHAT_ERROR_MESSAGES.unavailable)
        expect(run.res.body).to.not.match(/qdrant|cluster\.local/)
        expect(run.conversation().status).to.equal('Failed')
        expect(run.answer()?.content).to.equal(CHAT_ERROR_MESSAGES.unavailable)
      })

      it('reports an interrupted answer when the AI stream ends without a final answer', async () => {
        const run = await startStream(flow)

        run.ai.send('TEXT_MESSAGE_CONTENT', delta('Some text'))
        run.ai.finish()
        await run.res.ended
        await settle()

        expect(run.res.eventsOf('RUN_ERROR').map((e) => e.data.message)).to.deep.equal([CHAT_ERROR_MESSAGES.interrupted])
        expect(run.conversation().status).to.equal('Failed')
        expect(run.conversation().failReason).to.equal(CHAT_ERROR_MESSAGES.interrupted)
      })

      if (!flow.regenerate) {
        it('passes a sub-agent’s RUN_FINISHED through without treating it as the final answer', async () => {
          const run = await startStream(flow)

          run.ai.send('RUN_FINISHED', { runId: 'run-child', parentRunId: RUN })
          run.ai.send('RUN_FINISHED', finalAnswer('Done.'))
          run.ai.finish()
          await run.res.ended

          const finished = run.res.eventsOf('RUN_FINISHED')
          expect(finished).to.have.length(2)
          expect(finished[0]?.data.runId).to.equal('run-child')
          expect(finished[1]?.data.result).to.have.property('conversation')
          expect(run.answer()?.content).to.equal('Done.')
        })

        it('saves a question the agent asks the user, so it is still there after a reload', async () => {
          const run = await startStream(flow)

          run.ai.send('CUSTOM', { name: 'ask_user_question', value: { toolData: { question: 'Which quarter?' } } })
          run.ai.send('RUN_FINISHED', finalAnswer('Waiting for your answer.'))
          run.ai.finish()
          await run.res.ended
          await settle()

          const toolCall = run.messages().find((m) => m.messageType === 'tool_call')
          expect(toolCall, 'saved tool_call message').to.not.equal(undefined)
          const tools = toolCall?.tools as Array<{ toolName: string; toolResult: unknown }>
          expect(tools[0]?.toolName).to.equal('ask_user_question')
          expect(tools[0]?.toolResult).to.deep.equal({ question: 'Which quarter?' })
          expect(run.res.eventsOf('CUSTOM').some((e) => e.data.name === 'ask_user_question')).to.equal(true)
        })
      }

      it('tells the user the answer could not be saved when the database write fails, and still closes the stream', async () => {
        const run = await startStream(flow)
        const write = (flow.regenerate ? ChatSessionMessage.findOneAndReplace : ChatSessionMessage.insertMany) as unknown as sinon.SinonStub
        write.onCall(write.callCount).rejects(new Error('E11000 duplicate key error collection: es.chatSessionMessages'))

        run.ai.send('RUN_FINISHED', finalAnswer('Lost answer.'))
        run.ai.finish()
        await Promise.race([run.res.ended, settle(40)])

        expect(run.res.writableEnded, 'stream closed').to.equal(true)
        expect(run.res.eventsOf('RUN_ERROR').map((e) => e.data.message)).to.deep.equal([CHAT_ERROR_MESSAGES.saveFailed])
        expect(run.res.body).to.not.contain('E11000')
        expect(run.conversation().status).to.equal('Failed')
      })

      it('still closes the stream with a plain message when the database is unreachable at the end of the answer', async () => {
        const run = await startStream(flow)
        const down = new Error('connection to mongo-0.internal:27017 closed')
        ;(ChatSessionMessage.insertMany as unknown as sinon.SinonStub).rejects(down)
        ;(ChatSessionMessage.findOneAndReplace as unknown as sinon.SinonStub).rejects(down)
        ;(ChatSession.prototype.save as unknown as sinon.SinonStub).rejects(down)

        run.ai.send('RUN_FINISHED', finalAnswer('Lost answer.'))
        run.ai.finish()
        await Promise.race([run.res.ended, settle(40)])

        expect(run.res.writableEnded, 'stream closed').to.equal(true)
        expect(run.res.eventsOf('RUN_ERROR').map((e) => e.data.message)).to.deep.equal([CHAT_ERROR_MESSAGES.saveFailed])
        expect(run.res.body).to.not.contain('mongo-0')
      })

      it('ignores malformed frames from the AI service instead of failing the answer', async () => {
        const run = await startStream(flow)

        run.ai.sendRaw('event: RUN_ERROR\ndata: {not json\n\n')
        run.ai.sendRaw('event: TEXT_MESSAGE_CONTENT\ndata: {not json\n\n')
        run.ai.sendRaw('event: CUSTOM\ndata: {not json\n\n')
        run.ai.send('RUN_FINISHED', finalAnswer('Still answered.'))
        run.ai.finish()
        await run.res.ended
        await settle()

        expect(run.answer()?.content).to.equal('Still answered.')
        expect(run.conversation().status).to.not.equal('Failed')
        expect(run.res.body).to.contain('{not json')
      })
    })
  }

  for (const flow of flows.filter((f) => f.regenerate)) {
    it(`${flow.name}: a database that refuses the connection while saving is reported as unavailable, once`, async () => {
      const run = await startStream(flow)
      const replace = ChatSessionMessage.findOneAndReplace as unknown as sinon.SinonStub
      replace.onCall(replace.callCount).rejects(
        Object.assign(new Error('connect ECONNREFUSED 10.0.0.7:27017'), { cause: { code: 'ECONNREFUSED' } }),
      )

      run.ai.send('RUN_FINISHED', finalAnswer('Lost answer.'))
      run.ai.finish()
      await Promise.race([run.res.ended, settle(40)])

      expect(run.res.eventsOf('RUN_ERROR').map((e) => e.data.message)).to.deep.equal([CHAT_ERROR_MESSAGES.unavailable])
      expect(run.answer()?.content).to.equal(CHAT_ERROR_MESSAGES.unavailable)
      expect(run.conversation().failReason).to.equal(CHAT_ERROR_MESSAGES.unavailable)
      expect(run.res.body).to.not.contain('10.0.0.7')
    })
  }

  it('streamChat: a request without a question is refused before anything is saved', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const res = new FakeSSEResponse()
    let error: unknown
    try {
      await controllerModule.streamChat(appConfig)({ body: {}, query: {}, user: { userId: OWNER, orgId: ORG } } as never, res as never)
    } catch (e) {
      error = e
    }
    expect((error as { statusCode?: number } | undefined)?.statusCode).to.equal(400)
    expect(store.sessions).to.have.length(0)
  })

  it('addMessageStream: the AI request carries earlier turns but not the question being asked', async () => {
    const run = await startStream(flows[1] as Flow)
    run.ai.finish()
    await run.res.ended

    const body = run.ai.streamCalls[0]?.body as { query: string; conversationId: string; previousConversations: Array<{ content: string }> }
    expect(body.query).to.equal('And the one before?')
    expect(body.conversationId).to.equal(run.prepared.params.conversationId)
    expect(body.previousConversations.map((p) => p.content)).to.deep.equal(['What changed in the release?', 'An older answer.'])
  })

  it('addMessageStream: a stack trace saved from an earlier failed turn never reaches the browser', async () => {
    const run = await startStream(flows[1] as Flow)
    run.conversation().set('conversationErrors', [
      { message: CHAT_ERROR_MESSAGES.failed, errorType: 'internal_error', stack: 'Error: boom\n    at /srv/pipeshub/secret-internal.ts:42' },
    ])

    run.ai.send('RUN_FINISHED', finalAnswer('Fine now.'))
    run.ai.finish()
    await run.res.ended

    const { conversation } = run.res.eventsOf('RUN_FINISHED')[0]?.data.result as { conversation: { conversationErrors: unknown[] } }
    expect(conversation.conversationErrors).to.have.length(1)
    expect(conversation.conversationErrors[0]).to.include({ message: CHAT_ERROR_MESSAGES.failed })
    expect(run.res.body).to.not.contain('secret-internal.ts')
    expect(run.conversation().conversationErrors?.[0]?.stack, 'the stack is still kept for the logs and admins').to.contain('secret-internal.ts')
  })

  it('regenerateAnswers: only the last answer of the conversation can be regenerated', async () => {
    const store = new InMemoryChatStore()
    const ai = new FakeAIBackend()
    store.install()
    ai.install()
    const { session } = seedConversation(store, false)
    const firstQuestion = store.messagesOf(session._id)[0] as { _id: Types.ObjectId }
    const res = new FakeSSEResponse()

    await controllerModule.regenerateAnswers(appConfig)(
      {
        headers: {},
        params: { conversationId: String(session._id), messageId: String(firstQuestion._id) },
        body: {},
        query: {},
        user: { userId: OWNER, orgId: ORG },
        context: {},
      } as never,
      res as never,
    )
    await settle()

    expect(res.eventsOf('RUN_ERROR')[0]?.data.message).to.equal('Can only regenerate the last message in the conversation')
    expect(ai.streamCalls).to.deep.equal([])
    expect(store.messagesOf(session._id).map((m) => m.content)).to.deep.equal(['What changed in the release?', 'An older answer.'])
  })
})
