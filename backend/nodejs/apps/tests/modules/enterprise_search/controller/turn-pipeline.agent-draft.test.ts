import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { ACTORS } from '../helpers/conversation-world'
import { Kind, TurnWorld, turnWorld } from '../helpers/turn-world'

const DRAFT = { draftId: 'd-1', name: 'Offer drafter', instructions: 'secret prompt', toolsets: [], requestedBy: 'x' }
const KINDS: Kind[] = ['chat', 'agent']

const finalMessages = (turn: { res: { events(): Array<{ event: string; data: Record<string, any> }> } }): Array<Record<string, any>> => {
  const done = turn.res.events().find((e) => e.event === 'RUN_FINISHED' && e.data.result?.conversation)
  return done!.data.result.conversation.messages
}

const seedDraftBy = (w: TurnWorld, kind: Kind, who: 'A' | 'B'): void => {
  const session = w.s.store.session(w.s.ids[kind])!
  w.s.store.addMessage(session, { messageType: 'tool_call', content: '', requestedBy: ACTORS[who].userId, tools: [{ toolName: 'draft_agent', toolResult: DRAFT }] })
}

describe('agent_draft events in a turn', () => {
  // The completion frame re-reads the whole conversation only when the driver is connected.
  beforeEach(() => {
    Object.defineProperty(mongoose.connection, 'readyState', { value: 1, configurable: true })
  })
  afterEach(() => {
    delete (mongoose.connection as any).readyState
    sinon.restore()
  })

  for (const kind of KINDS) {
    describe(kind, () => {
      it('PH11-persist: the draft is stored as a draft_agent tool_call row for its requester, before the answer, and reaches the browser', async () => {
        const w = turnWorld()
        const turn = await w.start('B', kind, 'stream')
        w.s.ai.send('CUSTOM', { name: 'agent_draft', value: DRAFT })
        await w.answerStream()
        await turn.res.ended

        const [, , question, card, answer] = w.rows(kind)
        expect(card!.messageType).to.equal('tool_call')
        expect((card as any).tools[0]).to.deep.include({ toolName: 'draft_agent' })
        expect((card as any).tools[0].toolResult).to.deep.equal(DRAFT)
        expect(String(card!.requestedBy)).to.equal(String(ACTORS.B.userId))
        expect(String(card!.inReplyTo)).to.equal(String(question!._id))
        expect(card!.seq).to.be.lessThan(answer!.seq)
        expect(turn.res.events().some((e) => e.event === 'CUSTOM' && e.data.name === 'agent_draft')).to.equal(true)
      })

      it('AB-09: the completion frame of another participant’s turn carries the earlier draft redacted; the requester’s own turn keeps it', async () => {
        const w = turnWorld()
        seedDraftBy(w, kind, 'A')
        const turn = await w.start('B', kind, 'stream')
        await w.answerStream()
        await turn.res.ended
        const theirs = finalMessages(turn)
        expect(JSON.stringify(theirs)).to.not.include('secret prompt')
        expect(theirs.find((m) => m.messageType === 'tool_call')!.tools[0].toolResult).to.deep.equal({ redacted: true, authorId: String(ACTORS.A.userId) })

        const own = turnWorld()
        seedDraftBy(own, kind, 'A')
        const mine = await own.start('A', kind, 'stream')
        await own.answerStream()
        await mine.res.ended
        expect(finalMessages(mine).find((m) => m.messageType === 'tool_call')!.tools[0].toolResult.instructions).to.equal('secret prompt')
      })

      it('regenerate stores a new draft for the requester and hides an earlier one from nobody who asked for it', async () => {
        const w = turnWorld()
        seedDraftBy(w, kind, 'B')
        const turn = await w.startRegenerate('A', kind)
        w.s.ai.send('CUSTOM', { name: 'agent_draft', value: DRAFT })
        await w.answerStream()
        await turn.res.ended

        const cards = w.rows(kind).filter((m) => m.messageType === 'tool_call')
        const fresh = cards.find((m) => String(m.requestedBy) === String(ACTORS.A.userId))!
        expect((fresh as any).tools[0].toolName).to.equal('draft_agent')
        const frame = finalMessages(turn).filter((m) => m.messageType === 'tool_call')
        const other = frame.find((m) => m.requestedBy && String(m.requestedBy) === String(ACTORS.B.userId))!
        expect(other.tools[0].toolResult).to.deep.equal({ redacted: true, authorId: String(ACTORS.B.userId) })
      })
    })
  }
})
