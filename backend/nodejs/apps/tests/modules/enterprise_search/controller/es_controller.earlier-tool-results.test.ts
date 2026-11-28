import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { InMemoryChatStore, oid, settle } from './chat-test-harness'
import { appConfig, finalAnswer, Flow, flows, ORG, OWNER, Prepared, Run, sendAgain, startStream } from './streaming-flows'
import { registerUpstreams } from '../../../../src/libs/http/upstream-call'
import { registerAppUpstreams } from '../../../../src/modules/tokens_manager/config/config'
import { ProjectsFeature } from '../../../../src/modules/projects/utils/projects-feature'
import { guardedProject } from '../helpers/guarded-chat'
import { redactConversationFor, viewerOf } from '../../../../src/modules/enterprise_search/utils/viewer-redaction'

/**
 * In a chat others read, a later turn's model is given the earlier answers' text, never what their tools returned
 * (the route today's loop answers on). Ada's first turn searched; the search returned a private document beside a
 * public one, and her answer cited the public one. Turns two to four ask about what the first one found: the request
 * each sends the AI service carries the earlier questions and answers and none of the search's results, so a fact
 * the first answer did not state can only come from a fresh search, which that turn cites. A chat nobody else reads
 * still sends the earlier results, as before.
 */

const PRIVATE = 'Alpha budget 4.2M, the board has not seen it'
const PUBLIC_ANSWER = 'The public Charlie plan lists three themes.'
const BEN = oid()
const PROJECT = oid()
const ASK = 'internaltools__ask_user_question'

type HistoryTurn = { role: string; content: string; tool_results?: Array<{ tool_name?: string }>; referenceData?: unknown }

const REACH: Record<string, Record<string, unknown>> = {
  'shared with someone': { isShared: true, sharedWith: [{ userId: BEN, accessLevel: 'read' }] },
  'open to its project': { projectId: PROJECT, projectVisibility: 'project' },
  'that nobody else reads': {},
  'unshared, its old share rows kept': { isShared: false, sharedWith: [{ userId: BEN, accessLevel: 'read' }] },
}

/** Ada's chat after her first turn, `reach` deciding who else reads it. */
const adaSearched = (base: Flow, reach: Record<string, unknown>): Flow => ({
  ...base,
  prepare: (store: InMemoryChatStore): Prepared => {
    const session = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Plans', ...reach })
    store.addMessage(session, { messageType: 'user_query', content: 'what do our plans say?' })
    store.addMessage(session, {
      messageType: 'bot_response',
      content: PUBLIC_ANSWER,
      parts: [
        {
          type: 'tool_call',
          toolCallId: 'c1',
          toolName: 'search',
          status: 'completed',
          resultPreview: `Alpha: ${PRIVATE}. Charlie: three themes.`,
          resultSummary: `2 results: Alpha (${PRIVATE}), Charlie`,
        },
        { type: 'tool_call', toolCallId: 'q1', toolName: ASK, status: 'completed', resultPreview: '{"answers":["EU"]}' },
        { type: 'text', content: PUBLIC_ANSWER },
      ],
      referenceData: [{ name: 'Alpha plan', id: 'a1', type: 'record' }],
    })
    if (base.regenerate) {
      // A regenerate of the second answer: the first, with its search, is the history it is given.
      store.addMessage(session, { messageType: 'user_query', content: 'what did the Alpha result say?' })
      const second = store.addMessage(session, { messageType: 'bot_response', content: 'I would search again for that.' })
      store.writes.length = 0
      return {
        params: { conversationId: String(session._id), messageId: String(second._id) },
        body: {},
        existing: session,
        replacedMessageId: String(second._id),
      }
    }
    store.writes.length = 0
    return {
      params: { conversationId: String(session._id) },
      body: { query: 'what did the Alpha result say?' },
      existing: session,
    }
  },
})

const historyOf = (run: Run, call: number): HistoryTurn[] =>
  (run.ai.streamCalls[call]?.body as { previousConversations: HistoryTurn[] }).previousConversations

/** Finishes the stream the AI service has open with `answer`. */
async function answer(run: Run, text: string, done: Promise<unknown>): Promise<void> {
  run.ai.send('RUN_FINISHED', finalAnswer(text))
  run.ai.finish()
  await done
  await settle()
}

const addMessage = flows.find((f) => f.name === 'addMessageStream') as Flow
const regenerate = flows.find((f) => f.name === 'regenerateAnswers') as Flow

describe('a chat others read: a later turn is given the earlier answers, not what their tools returned', () => {
  beforeEach(() => {
    registerAppUpstreams(appConfig)
    // The project's own context is not what this is about: whoever may open the chat through it reads it all the same.
    sinon.stub(ProjectsFeature, 'isEnabled').resolves(false)
    // Ben reads it as a member of its project.
    guardedProject({ _id: PROJECT, orgId: ORG, userId: OWNER }, [BEN])
  })
  afterEach(() => {
    registerUpstreams({})
    sinon.restore()
  })

  for (const reach of ['shared with someone', 'open to its project']) {
    it(`${reach}: turns two to four send the questions and answers, and nothing of the search`, async () => {
      const flow = adaSearched(addMessage, REACH[reach] as Record<string, unknown>)
      const run = await startStream(flow, undefined, undefined, { wait: false })
      await answer(run, 'I would search again for that.', run.done)
      for (const query of ['and its budget?', 'list everything you found']) {
        run.prepared.body = { query }
        const next = sendAgain(flow, run)
        await settle()
        await answer(run, 'I would search again for that.', next.done)
      }

      expect(run.ai.streamCalls).to.have.length(3)
      for (let call = 0; call < 3; call += 1) {
        const history = historyOf(run, call)
        expect(JSON.stringify(history), `turn ${call + 2}`).to.not.include(PRIVATE)
        expect(history.some((turn) => turn.referenceData !== undefined), `turn ${call + 2}`).to.equal(false)
        // The question card Ada answered is her own words, and stays.
        expect(history[1]?.tool_results?.map((r) => r.tool_name)).to.deep.equal([ASK])
        expect(history.map((turn) => turn.content).slice(0, 2)).to.deep.equal(['what do our plans say?', PUBLIC_ANSWER])
      }
      expect(historyOf(run, 2).map((turn) => turn.content)).to.deep.equal([
        'what do our plans say?',
        PUBLIC_ANSWER,
        'what did the Alpha result say?',
        'I would search again for that.',
        'and its budget?',
        'I would search again for that.',
      ])
    })
  }

  for (const reach of ['that nobody else reads', 'unshared, its old share rows kept']) {
    it(`a chat ${reach} still sends the earlier search to the next turn`, async () => {
      const run = await startStream(adaSearched(addMessage, REACH[reach] as Record<string, unknown>), undefined, undefined, { wait: false })
      await answer(run, 'It is 4.2M.', run.done)
      const history = historyOf(run, 0)
      expect(JSON.stringify(history)).to.include(PRIVATE)
      expect(history[1]?.tool_results?.map((r) => r.tool_name)).to.deep.equal(['search', ASK])
      expect(history[1]?.referenceData).to.deep.equal([{ name: 'Alpha plan', id: 'a1', type: 'record' }])
    })
  }

  it('a regenerate in a chat others read is given the earlier answers the same way', async () => {
    const flow = adaSearched(regenerate, REACH['shared with someone'] as Record<string, unknown>)
    const run = await startStream(flow, undefined, undefined, { wait: false })
    await answer(run, 'Regenerated.', run.done)
    expect(run.ai.streamCalls).to.have.length(1)
    const history = historyOf(run, 0)
    expect(history.map((turn) => turn.content)).to.deep.equal(['what do our plans say?', PUBLIC_ANSWER])
    expect(JSON.stringify(history)).to.not.include(PRIVATE)
  })

  describe('the turns first, then the share: an answer given the earlier search says so, and readers never get it', () => {
    /** The chat as `who` reads it once it is shared with Ben. */
    const readAs = async (run: Run, who: unknown): Promise<Array<Record<string, unknown>>> => {
      const chat = run.conversation()
      const out = await redactConversationFor(appConfig, viewerOf(String(who), String(ORG)), {
        _id: chat._id,
        orgId: ORG,
        userId: OWNER,
        initiator: OWNER,
        isShared: true,
        sharedWith: [{ userId: BEN, accessLevel: 'read' }],
        messages: run.messages(),
      })
      return (out.messages as Array<Record<string, unknown>>).filter((m) => m.messageType === 'bot_response')
    }

    it('turns two to four, run while nobody else read the chat, are hidden from Ben once it is shared; later turns are not marked', async () => {
      const flow = adaSearched(addMessage, REACH['that nobody else reads'] as Record<string, unknown>)
      const run = await startStream(flow, undefined, undefined, { wait: false })
      await answer(run, 'It is 4.2M.', run.done)
      for (const query of ['and its board?', 'and the date?']) {
        run.prepared.body = { query }
        const next = sendAgain(flow, run)
        await settle()
        await answer(run, `${query} 4.2M.`, next.done)
      }
      // Each was given the first turn's search: each says so.
      const before = run.messages().filter((m) => m.messageType === 'bot_response')
      expect(before.map((m) => m.earlierResults)).to.deep.equal([undefined, true, true, true])

      const chat = run.conversation()
      chat.set('isShared', true)
      chat.set('sharedWith', [{ userId: BEN, accessLevel: 'read' }])
      run.prepared.body = { query: 'what changed?' }
      const after = sendAgain(flow, run)
      await settle()
      await answer(run, 'Nothing new.', after.done)
      expect(run.answer()?.earlierResults, 'a turn after the share is given the answers only').to.equal(undefined)

      const bens = await readAs(run, BEN)
      expect(bens[0]?.content).to.equal(PUBLIC_ANSWER)
      for (const hidden of bens.slice(1)) expect(hidden.answerHidden).to.deep.equal({ redacted: true, reason: 'no_access' })
      expect(JSON.stringify(bens)).to.not.include('4.2M')
      // Their asker reads every one.
      expect((await readAs(run, OWNER)).map((m) => m.content)).to.deep.equal([PUBLIC_ANSWER, 'It is 4.2M.', 'and its board? 4.2M.', 'and the date? 4.2M.', 'Nothing new.'])
    })

    it('a regenerate in a chat nobody else reads, given the earlier search, marks the answer it writes', async () => {
      const run = await startStream(adaSearched(regenerate, REACH['that nobody else reads'] as Record<string, unknown>), undefined, undefined, { wait: false })
      await answer(run, 'Regenerated: 4.2M.', run.done)
      expect(run.answer()?.earlierResults).to.equal(true)
    })

    it('a chat others read: no turn is given what earlier turns returned, and no answer is marked', async () => {
      const run = await startStream(adaSearched(addMessage, REACH['shared with someone'] as Record<string, unknown>), undefined, undefined, { wait: false })
      await answer(run, 'I would search again for that.', run.done)
      expect(run.answer()?.earlierResults).to.equal(undefined)
    })
  })

  it('the chat as it is when the turn starts decides: shared after the first turn, the next turn follows', async () => {
    const flow = adaSearched(addMessage, REACH['that nobody else reads'] as Record<string, unknown>)
    const run = await startStream(flow, undefined, undefined, { wait: false })
    await answer(run, 'It is 4.2M.', run.done)
    expect(JSON.stringify(historyOf(run, 0))).to.include(PRIVATE)

    const chat = run.conversation()
    chat.set('isShared', true)
    chat.set('sharedWith', [{ userId: BEN, accessLevel: 'read' }])
    run.prepared.body = { query: 'and its budget?' }
    const next = sendAgain(flow, run)
    await settle()
    await answer(run, 'I would search again for that.', next.done)
    expect(JSON.stringify(historyOf(run, 1))).to.not.include(PRIVATE)
  })
})
