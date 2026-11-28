import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { DefaultConversationGuards } from '../../../../../src/modules/enterprise_search/services/conversation/http/default-conversation-guards'
import { ConversationNotFoundError } from '../../../../../src/modules/enterprise_search/services/conversation/domain/errors'
import type { ConversationOperation } from '../../../../../src/modules/enterprise_search/services/conversation/domain/types'
import { ChatSession } from '../../../../../src/modules/enterprise_search/schema/chat.session.schema'

/**
 * Who may act on a chat as its owner, as main's lookups had it: sharing, the project link, delete, archive and
 * feedback on a chat, and regenerate, sharing and the project link on an agent's chat, take the person who both owns
 * the chat (`userId`) and started it (`initiator`). Everything else takes its owner.
 */

const ORG = new Types.ObjectId()
const OWNER = new Types.ObjectId()
const STARTER = new Types.ObjectId()
const AGENT_KEY = 'agent-1'

const leanOf = <T>(value: T) => ({ select: () => ({ lean: () => Promise.resolve(value) }) })

const guards = () =>
  new DefaultConversationGuards({
    projects: { accessibleProjectIds: sinon.stub().resolves([]), roleOf: sinon.stub(), assertAtLeast: sinon.stub() } as never,
  })

const chat = (initiator: Types.ObjectId, agent: boolean) => ({
  _id: new Types.ObjectId(),
  orgId: ORG,
  userId: OWNER,
  initiator,
  isDeleted: false,
  isShared: true,
  sharedWith: [],
  ...(agent && { agentKey: AGENT_KEY }),
})

async function decide(op: ConversationOperation, kind: 'chat' | 'agent', session: Record<string, unknown>): Promise<unknown> {
  sinon.stub(ChatSession, 'findOne').returns(leanOf(session) as never)
  let error: unknown = 'not called'
  const req = {
    params: { conversationId: String(session._id), ...(kind === 'agent' && { agentKey: AGENT_KEY }) },
    query: {},
    headers: {},
    user: { userId: String(OWNER), orgId: String(ORG) },
  }
  await guards().authorize(op, kind)(req as never, {} as never, ((err?: unknown) => {
    error = err
  }) as never)
  sinon.restore()
  return error
}

const ALL: ConversationOperation[] = [
  'read', 'feedback', 'leave', 'archiveSelf', 'send', 'cancel', 'regenerate', 'resume', 'invite', 'manageSharing',
  'settings', 'transfer', 'rename', 'linkProject', 'fork', 'delete',
]
const STARTER_ONLY: Record<'chat' | 'agent', ConversationOperation[]> = {
  chat: ['manageSharing', 'linkProject', 'delete', 'archiveSelf', 'feedback'],
  agent: ['regenerate', 'manageSharing', 'linkProject'],
}

describe('default guards: an owner who did not start the chat', () => {
  afterEach(() => sinon.restore())

  for (const kind of ['chat', 'agent'] as const) {
    it(`${kind}: may not do what main kept for the person who started it`, async () => {
      for (const op of STARTER_ONLY[kind]) {
        expect(await decide(op, kind, chat(STARTER, kind === 'agent')), op).to.be.instanceOf(ConversationNotFoundError)
      }
    })

    it(`${kind}: still does everything else an owner does`, async () => {
      for (const op of ALL.filter((o) => !STARTER_ONLY[kind].includes(o))) {
        expect(await decide(op, kind, chat(STARTER, kind === 'agent')), op).to.equal(undefined)
      }
    })

    it(`${kind}: who owns and started it does all of it`, async () => {
      for (const op of ALL) {
        expect(await decide(op, kind, chat(OWNER, kind === 'agent')), op).to.equal(undefined)
      }
    })
  }
})
