import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import {
  redactAgentDraft,
  redactAgentDrafts,
} from '../../../../src/modules/enterprise_search/services/collaboration/feed/draft-redaction'
import { formatPreviousConversations } from '../../../../src/modules/enterprise_search/utils/utils'
import { behindGuard, realGuards } from '../helpers/guarded-chat'
import { Fixture, PEOPLE, asUser, id, startFixture } from '../helpers/collab-fixture'
import { FakeAIBackend, FakeSSEResponse, InMemoryChatStore, oid, settle } from '../controller/chat-test-harness'

const A = String(PEOPLE.A)
const B = String(PEOPLE.B)
const DRAFT = { draftId: 'd-1', name: 'Offer drafter', instructions: 'secret prompt', toolsets: [], requestedBy: A }
const draftTool = [{ toolName: 'draft_agent', toolResult: DRAFT }]
const draftPart = {
  type: 'tool_call',
  toolCallId: 'c1',
  toolName: 'agent_builder__draft_agent',
  args: '{"instructions":"secret prompt"}',
  argsSummary: 'secret prompt',
  resultPreview: '{"draft":"secret prompt"}',
  resultSummary: 'secret prompt',
  status: 'completed',
}

describe('agent draft redaction', () => {
  describe('redactAgentDraft', () => {
    const row = { messageType: 'tool_call', requestedBy: PEOPLE.A, tools: draftTool }

    it('AB-09: another viewer gets {redacted, authorId}; the requester gets the draft', () => {
      const other = redactAgentDraft(row, B)
      expect(other.tools[0]!.toolResult).to.deep.equal({ redacted: true, authorId: A })
      expect(JSON.stringify(other)).to.not.include('secret prompt')
      expect(redactAgentDraft(row, A)).to.equal(row)
    })

    it('no viewer id means redacted, and the stored row is not mutated', () => {
      expect(JSON.stringify(redactAgentDraft(row, undefined))).to.not.include('secret prompt')
      expect(row.tools[0]!.toolResult).to.equal(DRAFT)
    })

    it('a row without a requester is redacted for everyone', () => {
      const orphan = { messageType: 'tool_call', tools: draftTool }
      expect(redactAgentDraft(orphan, A).tools[0]!.toolResult).to.deep.equal({ redacted: true })
    })

    it('scrubs the draft tool call out of a bot_response transcript, nested sub-agent parts included', () => {
      const bot = {
        messageType: 'bot_response',
        requestedBy: PEOPLE.A,
        parts: [{ type: 'text', content: 'hi' }, draftPart, { type: 'sub_agent', parts: [draftPart] }],
      }
      const seen = redactAgentDraft(bot, B)
      expect(JSON.stringify(seen)).to.not.include('secret prompt')
      expect(seen.parts[0]).to.deep.equal({ type: 'text', content: 'hi' })
      expect(seen.parts[1]).to.include({ toolName: 'agent_builder__draft_agent', status: 'completed', args: '{}' })
      expect(redactAgentDraft(bot, A)).to.equal(bot)
    })

    it('leaves rows without a draft alone', () => {
      const ask = { messageType: 'tool_call', requestedBy: PEOPLE.A, tools: [{ toolName: 'ask_user_question', toolResult: { q: 1 } }] }
      expect(redactAgentDraft(ask, B)).to.equal(ask)
      expect(redactAgentDrafts([ask], B)[0]).to.equal(ask)
    })

    it('history for the model carries the fact of a draft, not its contents', () => {
      const out = formatPreviousConversations([
        { messageType: 'user_query', content: 'make an agent' },
        { messageType: 'bot_response', content: 'Drafted.', parts: [draftPart] },
      ] as never)
      expect(JSON.stringify(out)).to.not.include('secret prompt')
      expect(JSON.stringify(out)).to.include('draft_agent')
    })
  })

  describe('the feed', () => {
    let f: Fixture
    afterEach(async () => {
      await f?.close()
      sinon.restore()
    })

    const seed = async () => {
      f = await startFixture()
      const chat = f.store.session(f.ids.chat)!
      chat.set('isShared', true)
      chat.set('sharedWith', [{ principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' }])
      f.store.addMessage(chat, { messageType: 'user_query', content: 'make an agent', authorUserId: PEOPLE.A })
      f.store.addMessage(chat, { messageType: 'tool_call', content: '', requestedBy: PEOPLE.A, tools: draftTool })
      f.store.addMessage(chat, { messageType: 'bot_response', content: 'Drafted.', requestedBy: PEOPLE.A, parts: [draftPart] })
    }
    const read = async (who: 'A' | 'B') => (await f.http.call('GET', f.path('chat', '/feed'), asUser(who))).body.messages as Array<Record<string, any>>

    it('AB-09: the requester sees the draft; another participant sees {redacted:true, authorId}', async () => {
      await seed()
      const mine = await read('A')
      expect(mine[1]!.tools[0].toolResult.instructions).to.equal('secret prompt')

      const theirs = await read('B')
      expect(theirs[1]!.tools[0].toolResult).to.deep.equal({ redacted: true, authorId: id('A') })
      expect(JSON.stringify(theirs)).to.not.include('secret prompt')
      expect(theirs[1]!.author.userId).to.equal(id('A'))
    })
  })

  describe('the conversation detail (PH11-11)', () => {
    const ORG = oid()
    const OWNER = oid()
    const READER = oid()
    afterEach(() => sinon.restore())

    const setup = (kind: 'chat' | 'agent') => {
      const store = new InMemoryChatStore()
      store.install()
      new FakeAIBackend().install()
      const session = store.addSession({
        orgId: ORG, userId: OWNER, initiator: OWNER, sessionType: kind, title: 't', isShared: true,
        sharedWith: [{ userId: READER, accessLevel: 'read' }], ...(kind === 'agent' && { agentKey: 'agent-1' }),
      })
      store.addMessage(session as never, { messageType: 'user_query', content: 'q' })
      store.addMessage(session as never, { messageType: 'tool_call', content: '', requestedBy: OWNER, tools: draftTool })
      store.addMessage(session as never, { messageType: 'bot_response', content: 'a', requestedBy: OWNER, parts: [draftPart] })
      return async (user: Types.ObjectId) => {
      const res = new FakeSSEResponse()
      const req = {
        headers: {}, params: { conversationId: String(session._id), ...(kind === 'agent' && { agentKey: 'agent-1' }) }, query: {}, body: {},
        user: { userId: String(user), orgId: String(ORG) }, context: { requestId: 'req' },
      }
      const appConfig = { aiBackend: 'http://ai.test', iamBackend: 'http://iam.test', jwtSecret: 'x', scopedJwtSecret: 'y' } as never
      const handler = (kind === 'agent' ? controller.getAgentConversationById : controller.getConversationById(appConfig)) as (a: never, b: never, c: never) => Promise<unknown>
      await behindGuard(realGuards({ collab: true }), 'read', handler, kind)(req as never, res as never, sinon.stub() as never)
      await settle()
      return (res.jsonBody as { conversation: { messages: Array<Record<string, any>> } }).conversation.messages
      }
    }

    for (const kind of ['chat', 'agent'] as const) {
      it(`${kind}: the requester reads the draft, a recipient gets the placeholder`, async () => {
        const open = setup(kind)
        const owner = await open(OWNER)
        expect(owner.find((m) => m.messageType === 'tool_call')!.tools[0].toolResult.instructions).to.equal('secret prompt')

        const reader = await open(READER)
        const card = reader.find((m) => m.messageType === 'tool_call')!
        expect(card.tools[0].toolResult).to.deep.equal({ redacted: true, authorId: String(OWNER) })
        expect(JSON.stringify(reader)).to.not.include('secret prompt')
      })
    }
  })
})
