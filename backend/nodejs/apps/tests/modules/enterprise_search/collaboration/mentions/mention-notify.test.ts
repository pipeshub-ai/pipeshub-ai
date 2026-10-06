import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { COLLAB_TYPES } from '../../../../../src/modules/enterprise_search/services/collaboration/collab.types'
import {
  IConversationEventProducers,
  useConversationEventProducers,
} from '../../../../../src/modules/enterprise_search/services/collaboration/notify/conversation-event-producers'
import { ChatMentionedEvent } from '../../../../../src/modules/enterprise_search/services/collaboration/notify/events'
import { Kind, TurnWorld, turnWorld } from '../../helpers/turn-world'
import { assistant, idOf, user } from './mention-world'

const KINDS: Kind[] = ['chat', 'agent']
const mentionedEvents = (w: TurnWorld): ChatMentionedEvent[] => w.routers().chat ? (w.env().collaboration.notifier.events.filter((e) => e.type === 'chat.mentioned') as ChatMentionedEvent[]) : []
const spy = (): { producers: IConversationEventProducers; calls: Array<Parameters<IConversationEventProducers['mentioned']>[0]>; failWith?: Error } => {
  const calls: Array<Parameters<IConversationEventProducers['mentioned']>[0]> = []
  const state: ReturnType<typeof spy> = {
    calls,
    producers: {
      turnEnded: async () => undefined,
      conversationDeleted: async () => undefined,
      mentioned: async (args) => {
        calls.push(args)
        if (state.failWith) throw state.failWith
      },
    },
  }
  return state
}
const settle = async (): Promise<void> => {
  for (let i = 0; i < 20; i += 1) await new Promise((r) => setImmediate(r))
}

describe('chat.mentioned from a stored message (PR-10.5)', () => {
  afterEach(() => {
    useConversationEventProducers(undefined)
    sinon.restore()
  })

  for (const kind of KINDS) {
    describe(kind, () => {
      it('MN-07: a note mentioning Carol publishes chat.mentioned for Carol, from Bob, keyed by the note', async () => {
        const w = turnWorld({}, { mentions: {} })
        const out = await w.request('B', kind, 'POST', '/notes', { query: 'FYI Carol', mentions: [user('C')], clientMessageId: 'n1' })
        expect(out.status).to.equal(201)
        const events = mentionedEvents(w)
        expect(events).to.have.length(1)
        expect(events[0]).to.deep.include({ actorUserId: idOf('B'), messageId: (out.body as any).note.id })
        expect(events[0]!.principals).to.deep.equal([{ type: 'user', userId: idOf('C') }])
        expect(events[0]!.ref.kind).to.equal(kind)
      })

      it('a replayed clientMessageId publishes again (same message, same keys), so a failed first publish is healed by the client retry', async () => {
        const w = turnWorld({}, { mentions: {} })
        const body = { query: 'FYI Carol', mentions: [user('C')], clientMessageId: 'n1' }
        w.env().collaboration.notifier.failWith = new Error('outbox down')
        const failed = await w.request('B', kind, 'POST', '/notes', body)
        expect(failed.status).to.equal(500)
        expect(w.rows(kind).filter((r) => r.messageType === 'note'), 'the note is stored').to.have.length(1)
        w.env().collaboration.notifier.failWith = undefined
        const retry = await w.request('B', kind, 'POST', '/notes', body)
        expect(retry.status).to.equal(200)
        expect(mentionedEvents(w)).to.have.length(1)
        expect(mentionedEvents(w)[0]!.messageId).to.equal((retry.body as any).note.id)
      })

      it('a note that fails validation or is refused publishes nothing', async () => {
        const w = turnWorld({}, { mentions: {} })
        await w.request('B', kind, 'POST', '/notes', { query: 'x', mentions: [user('D')], clientMessageId: 'n1' })
        await w.request('B', kind, 'POST', '/notes', { query: 'x', mentions: [user('C'), assistant], clientMessageId: 'n2' })
        expect(mentionedEvents(w)).to.have.length(0)
      })

      it('a question mentioning Carol and the assistant hands both to the producer once the question is stored', async () => {
        const w = turnWorld({}, { mentions: {} })
        const { producers, calls } = spy()
        useConversationEventProducers(producers)
        const turn = await w.start('B', kind, 'stream', { mentions: [user('C'), assistant] })
        expect(turn.reached).to.equal(true)
        await settle()
        expect(calls).to.have.length(1)
        const row = w.rows(kind).filter((r) => r.messageType === 'user_query').at(-1)!
        expect(calls[0]).to.deep.include({ messageId: String(row._id), actorUserId: idOf('B') })
        expect(calls[0]!.mentions).to.deep.equal([{ type: 'user', id: idOf('C') }, { type: 'assistant', id: 'self' }])
      })

      it('a question with no mentions never reaches the producer', async () => {
        const w = turnWorld({}, { mentions: {} })
        const { producers, calls } = spy()
        useConversationEventProducers(producers)
        await w.start('B', kind, 'stream')
        await settle()
        expect(calls).to.have.length(0)
      })

      it('a failing publish never fails the turn', async () => {
        const w = turnWorld({}, { mentions: {} })
        const s = spy()
        s.failWith = new Error('outbox down')
        useConversationEventProducers(s.producers)
        const turn = await w.start('B', kind, 'stream', { mentions: [user('C'), assistant] })
        await settle()
        expect(turn.reached).to.equal(true)
        expect(turn.error).to.equal(undefined)
        expect(s.calls).to.have.length(1)
      })
    })
  }
})
