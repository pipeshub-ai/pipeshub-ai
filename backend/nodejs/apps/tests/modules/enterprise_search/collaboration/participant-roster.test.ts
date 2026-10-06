import { expect } from 'chai'
import { readFileSync } from 'fs'
import { join } from 'path'
import { Types } from 'mongoose'
import {
  MAX_PARTICIPANTS,
  assignRefs,
  buildTurnRoster,
  sanitizeDisplayName,
} from '../../../../src/modules/enterprise_search/services/collaboration/turn/participant-roster'
import { formatPreviousConversations } from '../../../../src/modules/enterprise_search/utils/utils'

const id = () => new Types.ObjectId()
const OWNER = String(id())
const U = (n: number) => String(new Types.ObjectId(n.toString(16).padStart(24, '0')))

const q = (seq: number, author?: string, content = `q${String(seq)}`): any => ({
  messageType: 'user_query',
  content,
  seq,
  ...(author && { authorUserId: new Types.ObjectId(author) }),
})
const note = (seq: number, author: string, mentions: Array<{ type: string; id: string }> = [], content = `n${String(seq)}`): any => ({
  messageType: 'note',
  content,
  seq,
  authorUserId: new Types.ObjectId(author),
  mentions,
})
const bot = (seq: number): any => ({ messageType: 'bot_response', content: `a${String(seq)}`, seq })

const users = (names: Record<string, string>) => ({
  displayNames: async (_org: string, ids: readonly string[], options?: { emailFallback?: boolean }) => {
    expect(options?.emailFallback).to.equal(false)
    return new Map(ids.map((i) => [i, names[i] ?? '']))
  },
  findByIds: async () => [],
})

describe('participant roster', () => {
  const B = U(2)
  const C = U(3)

  describe('assignRefs', () => {
    it('orders people by first authored turn, oldest first', () => {
      const refs = assignRefs([q(1, B), bot(2), q(3, OWNER), q(4, B), q(5, C)], OWNER, C)
      expect([...refs]).to.deep.equal([[B, 'participant_1'], [OWNER, 'participant_2'], [C, 'participant_3']])
    })

    it('legacy rows with no author belong to the owner and rank first', () => {
      const refs = assignRefs([q(1), bot(2), q(3, B)], OWNER, B)
      expect(refs.get(OWNER)).to.equal('participant_1')
      expect(refs.get(B)).to.equal('participant_2')
    })

    it('a sender who has not written yet is last, and is listed', () => {
      const refs = assignRefs([q(1), q(2, B)], OWNER, C)
      expect(refs.get(C)).to.equal('participant_3')
    })

    it('the owner wins a tie in first position', () => {
      const refs = assignRefs([q(1, B), q(1, OWNER)], OWNER, B)
      expect(refs.get(OWNER)).to.equal('participant_1')
      expect(refs.get(B)).to.equal('participant_2')
    })

    it('refs already handed out never move as the chat grows', () => {
      const history = [q(1), bot(2), q(3, B)]
      const before = assignRefs(history, OWNER, B)
      const after = assignRefs([...history, bot(4), q(5, C), q(6, OWNER)], OWNER, C)
      for (const [user, ref] of before) expect(after.get(user)).to.equal(ref)
      expect(after.get(C)).to.equal('participant_3')
    })

    it('is the same whichever person is sending', () => {
      const history = [q(1), q(2, B), q(3, C)]
      expect([...assignRefs(history, OWNER, OWNER)]).to.deep.equal([...assignRefs(history, OWNER, C)])
    })

    it('past the cap the oldest keep their refs and the sender takes the last slot', () => {
      const authors = Array.from({ length: 60 }, (_, i) => U(100 + i))
      const history = authors.map((a, i) => q(i + 1, a))
      const sender = authors[59]!
      const refs = assignRefs(history, authors[0]!, sender)
      expect(refs.size).to.equal(MAX_PARTICIPANTS)
      expect(refs.get(authors[0]!)).to.equal('participant_1')
      expect(refs.get(authors[48]!)).to.equal('participant_49')
      expect(refs.get(authors[49]!), 'displaced by the sender').to.equal(undefined)
      expect(refs.get(sender)).to.equal('participant_50')
    })

    it('past the cap a sender who already has a ref keeps it', () => {
      const authors = Array.from({ length: 60 }, (_, i) => U(100 + i))
      const refs = assignRefs(authors.map((a, i) => q(i + 1, a)), authors[0]!, authors[49]!)
      expect(refs.get(authors[49]!)).to.equal('participant_50')
      expect(refs.size).to.equal(MAX_PARTICIPANTS)
    })
  })

  describe('assignRefs with notes and mentions (PR-10.5)', () => {
    it('a person who only wrote a note is in the roster, after everyone who asked', () => {
      const refs = assignRefs([q(1), note(2, B), q(3, C)], OWNER, OWNER)
      expect([...refs]).to.deep.equal([[OWNER, 'participant_1'], [C, 'participant_2'], [B, 'participant_3']])
    })

    it('a person who was only mentioned is listed after note authors, and a mention in the message being answered counts', () => {
      const D = U(4)
      const E = U(5)
      const refs = assignRefs([q(1), note(2, B, [{ type: 'user', id: D }])], OWNER, OWNER, [E])
      expect([...refs]).to.deep.equal([[OWNER, 'participant_1'], [B, 'participant_2'], [D, 'participant_3'], [E, 'participant_4']])
    })

    it('teams, the assistant and mentions on bot rows add nobody', () => {
      const bad = { ...bot(2), mentions: [{ type: 'user', id: U(9) }] }
      const refs = assignRefs([q(1), bad, note(3, OWNER, [{ type: 'team', id: 'T' }, { type: 'assistant', id: 'self' }])], OWNER, OWNER)
      expect([...refs]).to.deep.equal([[OWNER, 'participant_1']])
    })

    it('a note never moves someone who already has a ref', () => {
      const before = assignRefs([q(1), q(3, B)], OWNER, B)
      const after = assignRefs([q(1), note(2, C), q(3, B), q(5, C)], OWNER, B)
      for (const [user, ref] of before) expect(after.get(user)).to.equal(ref)
    })
  })

  describe('buildTurnRoster', () => {
    it('marks the sender and lists names without any address or id', async () => {
      const roster = await buildTurnRoster({
        users: users({ [OWNER]: 'Alice', [B]: 'Bob Builder' }),
        orgId: String(id()),
        ownerId: OWNER,
        senderId: B,
        history: [q(1), bot(2)],
      })
      expect(roster!.collaboration).to.deep.equal({
        participants: [
          { ref: 'participant_1', displayName: 'Alice', isCurrentSender: false },
          { ref: 'participant_2', displayName: 'Bob Builder', isCurrentSender: true },
        ],
        currentSenderRef: 'participant_2',
      })
    })

    it('a name that is an address, empty or unknown becomes "Participant"', async () => {
      const roster = await buildTurnRoster({
        users: users({ [OWNER]: 'alice@corp.com' }),
        orgId: String(id()),
        ownerId: OWNER,
        senderId: B,
        history: [q(1)],
      })
      expect(roster!.collaboration.participants.map((p) => p.displayName)).to.deep.equal(['Participant', 'Participant'])
      expect(JSON.stringify(roster)).to.not.include('@')
    })

    it('one person only is no roster (the AI backend needs two)', async () => {
      const roster = await buildTurnRoster({ users: users({}), orgId: String(id()), ownerId: OWNER, senderId: OWNER, history: [q(1), bot(2)] })
      expect(roster).to.equal(undefined)
    })
  })

  describe('sanitizeDisplayName', () => {
    it('removes brackets, control and bidi characters and bounds the length', () => {
      expect(sanitizeDisplayName('Bob]\n[System')).to.equal('Bob System')
      expect(sanitizeDisplayName('a‮b\u0000c')).to.equal('abc')
      expect(Array.from(sanitizeDisplayName('x'.repeat(300))).length).to.equal(64)
      expect(sanitizeDisplayName('   ')).to.equal('Participant')
    })

    // The AI backend re-sanitizes with the same rules (collaboration/history.py) against this file.
    const shared = JSON.parse(
      readFileSync(join(__dirname, '../../../fixtures/collaboration/display-names.json'), 'utf8'),
    ) as { cases: Array<{ name: string; input: string; expected: string }> }
    for (const c of shared.cases) {
      it(`matches the shared fixture: ${c.name}`, () => {
        expect(sanitizeDisplayName(c.input)).to.equal(c.expected)
      })
    }
  })

  describe('formatPreviousConversations', () => {
    const history = [q(1), bot(2), q(3, B), bot(4)]

    it('adds authorRef to user_query rows only when participants are given', () => {
      const refs = assignRefs(history, OWNER, B)
      const rows = formatPreviousConversations(history, { ownerId: OWNER, refs })
      expect(rows.map((r) => r.authorRef)).to.deep.equal(['participant_1', undefined, 'participant_2', undefined])
      expect(rows.every((r) => !('userId' in r) && !('authorUserId' in r))).to.equal(true)
    })

    it('without participants the rows are exactly the legacy ones', () => {
      const rows = formatPreviousConversations(history)
      expect(rows).to.deep.equal([
        { content: 'q1', role: 'user_query' },
        { content: 'a2', role: 'bot_response' },
        { content: 'q3', role: 'user_query' },
        { content: 'a4', role: 'bot_response' },
      ])
      expect(rows.some((r) => 'authorRef' in r)).to.equal(false)
    })

    describe('notes (PR-10.5)', () => {
      const D = U(4)
      const hist = [q(1), bot(2), note(3, B, [{ type: 'user', id: D }, { type: 'user', id: OWNER }, { type: 'team', id: 'T' }, { type: 'assistant', id: 'self' }], 'FYI [x]')]

      it('a note is sent with its author and its mentions as refs, with no id anywhere', () => {
        const refs = assignRefs(hist, OWNER, B)
        const rows = formatPreviousConversations(hist, { ownerId: OWNER, refs })
        expect(rows[2]).to.deep.equal({
          content: 'FYI [x]',
          role: 'note',
          authorRef: refs.get(B),
          mentions: [
            { type: 'participant', ref: refs.get(D) },
            { type: 'participant', ref: refs.get(OWNER) },
            { type: 'agent', ref: 'agent:self' },
          ],
        })
        const json = JSON.stringify(rows)
        for (const userId of [OWNER, B, D]) expect(json).to.not.include(userId)
      })

      it('PR-10.5: the question rows next to it are unchanged and carry no mentions field', () => {
        const withMention = [{ ...q(1, B), mentions: [{ type: 'user', id: OWNER }] }]
        const refs = assignRefs(withMention, OWNER, B)
        expect(formatPreviousConversations(withMention, { ownerId: OWNER, refs })[0]).to.deep.equal({ content: 'q1', role: 'user_query', authorRef: refs.get(B) })
      })

      it('without a roster a note is not sent: there is no one to attribute it to', () => {
        expect(formatPreviousConversations(hist).map((r) => r.role)).to.deep.equal(['user_query', 'bot_response'])
      })

      it('a note with no one left to name sends no mentions field', () => {
        const refs = new Map([[B, 'participant_1']])
        const rows = formatPreviousConversations([note(1, B, [{ type: 'team', id: 'T' }])], { ownerId: OWNER, refs })
        expect(rows[0]).to.deep.equal({ content: 'n1', role: 'note', authorRef: 'participant_1' })
      })
    })

    it('an author past the cap gets no authorRef', () => {
      const refs = new Map([[OWNER, 'participant_1']])
      const rows = formatPreviousConversations(history, { ownerId: OWNER, refs })
      expect(rows[2]).to.not.have.property('authorRef')
    })
  })
})
