import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import * as sanitizer from '../../../../src/modules/enterprise_search/utils/answer-sanitizer'
import {
  appendMessages,
  attachMessages,
  buildAIResponseMessage,
  buildConversationResponse,
  retireMessagesById,
  saveCompleteConversation,
  updateMessageById,
} from '../../../../src/modules/enterprise_search/utils/utils'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { labelContentHash } from '../../../../src/modules/enterprise_search/services/conversation/privacy/label'
import { LEGACY_LABEL_VER } from '../../../../src/modules/enterprise_search/services/conversation/privacy/label.types'
import type { IAIResponse, IChatSessionDocument } from '../../../../src/modules/enterprise_search/types/conversation.interfaces'
import { InMemoryChatStore, settle } from '../controller/chat-test-harness'
import { appConfig, Flow, flows, RUN, Run, startStream } from '../controller/streaming-flows'
import { registerUpstreams } from '../../../../src/libs/http/upstream-call'
import { registerAppUpstreams } from '../../../../src/modules/tokens_manager/config/config'

type Row = Record<string, unknown>

/** Dana's answer read record R; the strings are unique so any copy of them that leaves the server is found. */
const DANAS_LABEL = {
  v: 5,
  ver: '',
  contentHash: '',
  direct: {
    records: ['rec-CANARY-R'],
    vrids: ['vrid-CANARY-R'],
    nodes: ['kb-CANARY'],
    saVrids: [],
    personal: [],
    agents: [],
    chat: [],
    projects: [],
    marks: [],
  },
  parents: [],
  complete: true,
  tagset: 3,
  pub: false,
}

const labelledAnswer = (answer: string, extra: Row = {}): Row => ({
  runId: RUN,
  result: { answer, citations: [], confidence: 'High', provenance: DANAS_LABEL, ...extra },
})

const finish = async (run: Run, result: Row): Promise<void> => {
  run.ai.send('RUN_FINISHED', result)
  run.ai.finish()
  await run.res.ended
  await settle()
}

/** Every byte the asker's stream received, as text. */
const leaksLabel = (text: string): boolean =>
  text.includes('CANARY') || text.includes('"provenance"') || text.includes('"labelHistory"')

const finalConversation = (run: Run): Row => {
  const finished = run.res.eventsOf('RUN_FINISHED')
  expect(finished, 'one final frame').to.have.length(1)
  return (finished[0]?.data.result as { conversation: Row }).conversation
}

describe('answer labels in storage', () => {
  beforeEach(() => registerAppUpstreams(appConfig))
  afterEach(() => registerUpstreams({}))
  afterEach(() => sinon.restore())

  describe('a streamed answer', () => {
    for (const flow of flows) {
      it(`${flow.name}: the label the AI backend sent is stored on the answer and bound to its text`, async () => {
        const run = await startStream(flow)
        await finish(run, labelledAnswer('Revenue grew 12%.'))
        const answer = run.answer() as Row
        expect(answer.content).to.equal('Revenue grew 12%.')
        expect(answer.labelVer).to.match(/^[0-9a-f]{24}$/)
        const provenance = answer.provenance as Row
        expect(provenance.ver).to.equal(answer.labelVer)
        expect(provenance.contentHash).to.equal(labelContentHash(answer.content as string, answer.parts))
        expect((provenance.direct as Row).records).to.deep.equal(['rec-CANARY-R'])
      })

      it(`${flow.name}: no label field reaches the asker's stream, the final frame included`, async () => {
        const run = await startStream(flow)
        await finish(run, labelledAnswer('Revenue grew 12%.'))
        expect(leaksLabel(run.res.body), run.res.body).to.equal(false)
        for (const message of finalConversation(run).messages as Row[]) {
          expect(message).to.not.have.any.keys('provenance', 'labelHistory')
        }
      })
    }
  })

  describe('a run that fails', () => {
    for (const flow of flows) {
      it(`${flow.name}: the label the AI backend sent with its error is stored on the error row, and never sent on`, async () => {
        const run = await startStream(flow)
        run.ai.send('RUN_ERROR', { runId: RUN, message: 'The model is down.', code: 'internal_error', provenance: DANAS_LABEL })
        run.ai.finish()
        await run.res.ended
        await settle()
        const failed = run.answer() as Row
        expect(failed.messageType).to.equal('error')
        expect(failed.labelVer).to.match(/^[0-9a-f]{24}$/)
        const provenance = failed.provenance as Row
        expect(provenance.ver).to.equal(failed.labelVer)
        expect((provenance.direct as Row).records).to.deep.equal(['rec-CANARY-R'])
        expect(leaksLabel(run.res.body), run.res.body).to.equal(false)
      })

      it(`${flow.name}: an error sent with no label leaves the row its asker's alone`, async () => {
        const run = await startStream(flow)
        run.ai.send('RUN_ERROR', { runId: RUN, message: 'The model is down.', code: 'internal_error' })
        run.ai.finish()
        await run.res.ended
        await settle()
        const failed = run.answer() as Row
        expect(failed.messageType).to.equal('error')
        expect(failed.provenance).to.equal(undefined)
      })
    }
  })

  describe('regenerating an answer', () => {
    for (const flow of flows.filter((f: Flow) => f.regenerate)) {
      it(`${flow.name}: an answer written before labels is archived as the legacy version`, async () => {
        const run = await startStream(flow)
        await finish(run, labelledAnswer('A better answer.'))
        const answer = run.answer() as Row
        expect(answer.labelHistory).to.deep.equal([{ ver: LEGACY_LABEL_VER }])
        expect((answer.provenance as Row).ver).to.equal(answer.labelVer)
      })

      it(`${flow.name}: the label of the version it replaces is kept whole, with that version's id`, async () => {
        const oldLabel = { ...DANAS_LABEL, ver: 'aaaaaaaaaaaaaaaaaaaaaaaa', contentHash: 'h0', direct: { ...DANAS_LABEL.direct, records: ['rec-OLD'] } }
        const labelled: Flow = {
          ...flow,
          prepare: (store: InMemoryChatStore) => {
            const prepared = flow.prepare(store)
            const old = store.messages.find((m) => String(m._id) === prepared.replacedMessageId)
            old?.set({ labelVer: 'aaaaaaaaaaaaaaaaaaaaaaaa', provenance: oldLabel, runId: 'run-before' })
            return prepared
          },
        }
        const run = await startStream(labelled)
        await finish(run, labelledAnswer('A better answer.'))
        const answer = run.answer() as Row
        expect(answer.labelHistory).to.deep.equal([{ ver: 'aaaaaaaaaaaaaaaaaaaaaaaa', runId: 'run-before', provenance: oldLabel }])
        expect(answer.labelVer).to.not.equal('aaaaaaaaaaaaaaaaaaaaaaaa')
        expect((answer.provenance as Row).ver).to.equal(answer.labelVer)
        expect(leaksLabel(run.res.body), run.res.body).to.equal(false)
      })
    }
  })

  describe('the plain JSON answer and error answers', () => {
    const conversation = (store: InMemoryChatStore): IChatSessionDocument =>
      store.addSession({ orgId: new mongoose.Types.ObjectId(), userId: new mongoose.Types.ObjectId(), initiator: new mongoose.Types.ObjectId(), title: 'x' }) as never

    it('stores the label of a JSON answer and returns the conversation without it', async () => {
      const store = new InMemoryChatStore()
      store.install()
      const session = conversation(store)
      const data = { answer: 'Revenue grew 12%.', citations: [], confidence: 'High', provenance: DANAS_LABEL } as unknown as IAIResponse
      const saved = (await saveCompleteConversation(session, data, String(session.orgId))) as Row
      const stored = store.messagesOf(session._id).slice(-1)[0] as Row
      expect((stored.provenance as Row).ver).to.equal(stored.labelVer)
      expect(leaksLabel(JSON.stringify(saved))).to.equal(false)
    })

    it('an error answer is labelled too', async () => {
      const store = new InMemoryChatStore()
      store.install()
      const session = conversation(store)
      const data = { answer: 'The model refused.', answerMatchType: 'Error', errorCode: 'auth_error', citations: [], provenance: DANAS_LABEL } as unknown as IAIResponse
      await saveCompleteConversation(session, data, String(session.orgId))
      const stored = store.messagesOf(session._id).slice(-1)[0] as Row
      expect(stored.messageType).to.equal('error')
      expect((stored.provenance as Row).ver).to.equal(stored.labelVer)
    })

    it('buildAIResponseMessage copies the label the AI backend sent, checked', () => {
      const message = buildAIResponseMessage({ statusCode: 200, data: { answer: 'A.', citations: [], provenance: { ...DANAS_LABEL, v: 9 } } as never })
      expect(message.provenance?.complete).to.equal(false)
      expect(message.provenance?.incomplete).to.include('label_unknown_version')
    })
  })

  describe('writes', () => {
    const session = (store: InMemoryChatStore) =>
      store.addSession({ orgId: new mongoose.Types.ObjectId(), userId: new mongoose.Types.ObjectId(), initiator: new mongoose.Types.ObjectId() })

    it('an insert returns its rows without their label', async () => {
      const store = new InMemoryChatStore()
      store.install()
      const s = session(store)
      const [inserted] = await appendMessages(s._id, s.orgId, [buildAIResponseMessage({ statusCode: 200, data: { answer: 'A.', citations: [], provenance: DANAS_LABEL } as never })])
      const returned = (inserted as unknown as mongoose.Document).toObject() as Row
      expect(returned).to.not.have.any.keys('provenance', 'labelHistory')
      expect(returned.labelVer).to.be.a('string')
      expect((store.messagesOf(s._id)[0]?.provenance as Row).ver).to.equal(returned.labelVer)
    })

    it('a replacement returns its row without its label', async () => {
      const store = new InMemoryChatStore()
      store.install()
      const s = session(store)
      const old = store.addMessage(s, { messageType: 'bot_response', content: 'Old.' })
      const next = buildAIResponseMessage({ statusCode: 200, data: { answer: 'New.', citations: [], provenance: DANAS_LABEL } as never })
      const replaced = await updateMessageById(old._id, s.orgId, next)
      const returned = (replaced as unknown as mongoose.Document).toObject() as Row
      expect(returned).to.not.have.any.keys('provenance', 'labelHistory')
      const stored = store.messagesOf(s._id)[0] as Row
      expect(stored.labelHistory).to.deep.equal([{ ver: LEGACY_LABEL_VER }])
    })

    it('removing an answer archives its label with the time it was retired and clears it', async () => {
      const store = new InMemoryChatStore()
      store.install()
      const s = session(store)
      const row = store.addMessage(s, { messageType: 'bot_response', content: 'Old.', labelVer: 'bbbbbbbbbbbbbbbbbbbbbbbb', provenance: { ...DANAS_LABEL, ver: 'bbbbbbbbbbbbbbbbbbbbbbbb' }, runId: 'run-9' })
      expect(await retireMessagesById([row._id], { sessionId: s._id, orgId: s.orgId })).to.equal(1)
      const stored = store.messagesOf(s._id)[0] as Row
      expect(stored).to.not.have.property('provenance')
      expect(stored.content).to.equal('')
      expect(stored.labelVer).to.match(/^[0-9a-f]{24}$/)
      expect(stored.labelVer).to.not.equal('bbbbbbbbbbbbbbbbbbbbbbbb')
      const [entry] = stored.labelHistory as Row[]
      expect(entry).to.deep.include({ ver: 'bbbbbbbbbbbbbbbbbbbbbbbb', runId: 'run-9' })
      expect((entry?.provenance as Row).direct).to.deep.equal(DANAS_LABEL.direct)
      expect(entry?.retiredAt).to.be.instanceOf(Date)
    })

    it('removing a card leaves it without a label, as before', async () => {
      const store = new InMemoryChatStore()
      store.install()
      const s = session(store)
      const card = store.addMessage(s, { messageType: 'tool_call', content: '', tools: [{ toolName: 'ask_user_question', toolResult: {} }] })
      expect(await retireMessagesById([card._id], { sessionId: s._id, orgId: s.orgId })).to.equal(1)
      const stored = store.messagesOf(s._id)[0] as Row
      expect(stored).to.not.have.any.keys('labelVer', 'labelHistory', 'provenance')
      expect(stored.removedAt).to.be.instanceOf(Date)
    })

    it('stores the text the answer sanitiser gives back, and hashes that text', async () => {
      sinon.stub(sanitizer, 'sanitizeAnswerMarkdown').callsFake((text: string) => text.replace('https://evil.test/x.png', '#'))
      const store = new InMemoryChatStore()
      store.install()
      const s = session(store)
      await appendMessages(s._id, s.orgId, [buildAIResponseMessage({ statusCode: 200, data: { answer: '![](https://evil.test/x.png)', citations: [], provenance: DANAS_LABEL } as never })])
      const stored = store.messagesOf(s._id)[0] as Row
      expect(stored.content).to.equal('![](#)')
      expect((stored.provenance as Row).contentHash).to.equal(labelContentHash('![](#)', stored.parts))
    })
  })

  describe('responses', () => {
    const labelledRow = (): Row => ({
      _id: new mongoose.Types.ObjectId(),
      messageType: 'bot_response',
      content: 'A.',
      citations: [],
      labelVer: 'cccccccccccccccccccccccc',
      provenance: DANAS_LABEL,
      labelHistory: [{ ver: 'legacy' }],
    })

    it('a conversation built for a response carries no label field', () => {
      const built = attachMessages({ _id: 's1', title: 'T' }, [labelledRow()], 'u1') as Row
      expect(leaksLabel(JSON.stringify(built))).to.equal(false)
    })

    it('the conversation detail carries no label field', () => {
      const conversation = { _id: 's1', title: 'T', initiator: new mongoose.Types.ObjectId(), sharedWith: [] } as unknown as IChatSessionDocument
      const pagination = { page: 1, limit: 10, skip: 0, totalMessages: 1, hasNextPage: false, hasPrevPage: false }
      const built = buildConversationResponse(conversation, 'u1', pagination, [labelledRow() as never])
      expect(leaksLabel(JSON.stringify(built))).to.equal(false)
    })
  })

  describe('the stored message schema', () => {
    it('never reads a label unless asked for it; the version id is an ordinary field', () => {
      expect(ChatSessionMessage.schema.path('provenance')?.options.select).to.equal(false)
      expect(ChatSessionMessage.schema.path('labelHistory')?.options.select).to.equal(false)
      expect(ChatSessionMessage.schema.path('labelVer')?.options.select).to.not.equal(false)
    })
  })
})
