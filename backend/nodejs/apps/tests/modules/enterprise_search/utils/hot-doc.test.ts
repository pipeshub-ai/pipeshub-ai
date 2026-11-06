import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { EventEmitter } from 'events'
import mongoose from 'mongoose'
import {
  appendMessageFeedback,
  appendMessages,
  keepRunAlive,
  markConversationFailed,
} from '../../../../src/modules/enterprise_search/utils/utils'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { Logger } from '../../../../src/libs/services/logger.service'
import type { LeaseHandle } from '../../../../src/modules/enterprise_search/services/conversation/turn/run-lease'
import type { IChatSessionDocument, IMessage } from '../../../../src/modules/enterprise_search/types/conversation.interfaces'
import { InMemoryChatStore, oid } from '../controller/chat-test-harness'

/**
 * The session document is read and rewritten on every turn, so the writes to it stay few and its
 * arrays stay bounded.
 */

const ORG = oid()
const OWNER = oid()

function world(fields: Record<string, unknown> = {}) {
  const store = new InMemoryChatStore()
  store.install()
  const session = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, ...fields })
  // The handlers work on their own copy of the session, as they do after a read.
  return { store, session, doc: ChatSession.hydrate(session.toObject()) as unknown as IChatSessionDocument }
}

const question = (): IMessage => ({ messageType: 'user_query', content: 'q' }) as IMessage

describe('hot session document', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('an append moves lastActivityAt in the same write', () => {
    it('forward to now, with no second session write', async () => {
      const { store, session } = world({ lastActivityAt: 1 })
      const before = Date.now()

      await appendMessages(session._id, ORG, [question()])

      expect(store.session(session._id)?.get('lastActivityAt')).to.be.at.least(before)
      expect(store.writes.filter((w) => w.startsWith('chatSession.'))).to.deep.equal(['chatSession.allocateSeq'])
    })

    it('never backwards', async () => {
      const later = Date.now() + 60_000
      const { store, session } = world({ lastActivityAt: later })

      await appendMessages(session._id, ORG, [question()])

      expect(store.session(session._id)?.get('lastActivityAt')).to.equal(later)
    })
  })

  describe('keepRunAlive', () => {
    const lease = { runId: 'run-1' } as LeaseHandle

    it('writes nothing for a run that holds a lease', () => {
      const clock = sinon.useFakeTimers()
      const update = sinon.stub(ChatSession, 'updateOne').resolves({ matchedCount: 1 } as never)

      keepRunAlive(new EventEmitter(), oid(), undefined, lease)
      clock.tick(5 * 60_000)

      expect(update.called).to.equal(false)
    })

    it('still beats every minute for a run without one', () => {
      const clock = sinon.useFakeTimers()
      const update = sinon.stub(ChatSession, 'updateOne').resolves({ matchedCount: 1 } as never)
      const stream = new EventEmitter()

      keepRunAlive(stream, oid(), 'reservation-1')
      clock.tick(2 * 60_000)
      stream.emit('close')
      clock.tick(2 * 60_000)

      expect(update.callCount).to.equal(2)
    })
  })

  describe('conversationErrors', () => {
    it('keeps the newest 50', async () => {
      const old = Array.from({ length: 50 }, (_, i) => ({ message: `old ${String(i)}`, errorType: 'internal_error' }))
      const { store, session, doc } = world({ conversationErrors: old })

      await markConversationFailed(doc, 'It failed')

      const errors = store.session(session._id)?.toObject().conversationErrors as Array<{ message: string }>
      expect(errors).to.have.length(50)
      expect(errors[0]?.message).to.equal('old 1')
      expect(errors[49]?.message).to.equal('It failed')
    })

    it('logs a stack and does not store it', async () => {
      const logged = sinon.stub(Logger.prototype, 'error')
      const { store, session, doc } = world()

      await markConversationFailed(doc, 'It failed', null, 'internal_error', 'Error: boom\n    at handler.ts:1')

      const errors = store.session(session._id)?.toObject().conversationErrors as Array<{ stack?: string }>
      expect(errors[0]?.stack).to.equal(undefined)
      expect(JSON.stringify(logged.args)).to.contain('handler.ts:1')
    })
  })

  describe('feedback', () => {
    async function answered() {
      const w = world()
      const [bot] = await appendMessages(w.session._id, ORG, [{ messageType: 'bot_response', content: 'a' } as IMessage])
      const feedbackOf = (): Array<{ feedbackProvider: mongoose.Types.ObjectId; isHelpful?: boolean; comments?: { positive?: string } }> =>
        w.store.messagesOf(w.session._id)[0]?.feedback as never
      return { ...w, botId: bot!._id, feedbackOf }
    }

    it('keeps one entry per person: a second vote replaces the first, and leaves other people’s', async () => {
      const { botId, feedbackOf } = await answered()
      const other = oid()

      await appendMessageFeedback(botId, ORG, { isHelpful: true, feedbackProvider: OWNER })
      await appendMessageFeedback(botId, ORG, { isHelpful: true, feedbackProvider: other })
      await appendMessageFeedback(botId, ORG, { isHelpful: false, feedbackProvider: OWNER })

      expect(feedbackOf().map((f) => [String(f.feedbackProvider), f.isHelpful])).to.deep.equal([
        [String(other), true],
        [String(OWNER), false],
      ])
    })

    it('checks an entry against the schema before writing, and drops fields it does not know', async () => {
      const { botId, feedbackOf } = await answered()

      let caught: unknown
      try {
        await appendMessageFeedback(botId, ORG, { ratings: { accuracy: 9 }, feedbackProvider: OWNER })
      } catch (error) {
        caught = error
      }
      expect(caught).to.be.instanceOf(mongoose.Error.ValidationError)
      expect(feedbackOf()).to.deep.equal([])

      await appendMessageFeedback(botId, ORG, { isHelpful: true, feedbackProvider: OWNER, unknownField: 1 })
      expect(feedbackOf()[0]).to.not.have.property('unknownField')
    })

    it('stores text that looks like a field reference as written, never as an expression', async () => {
      const { botId } = await answered()

      await appendMessageFeedback(botId, ORG, { isHelpful: true, feedbackProvider: OWNER, comments: { positive: '$content' } })

      const [, pipeline] = (ChatSessionMessage.findOneAndUpdate as sinon.SinonStub).lastCall.args as [unknown, Array<Record<string, any>>]
      const added = pipeline[0]?.$set.feedback.$concatArrays[1]
      expect(added.$literal[0].comments).to.deep.equal({ positive: '$content' })
    })
  })
})
