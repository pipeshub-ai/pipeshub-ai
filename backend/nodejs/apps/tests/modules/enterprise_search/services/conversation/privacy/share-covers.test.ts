import { expect } from 'chai'
import {
  ON_BEHALF_OF_READER,
  consentCovers,
  fullOK,
  includableRows,
  readerView,
  resetVisibility,
  shareCovers,
  widenOK,
  type Reader,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import {
  AGENT_THREAD,
  BOB,
  CAROL,
  DANA,
  FORK,
  ORG,
  PRIYA,
  R,
  R2,
  ROOM,
  THREAD,
  WAYS,
  agentThread,
  answer,
  copyOf,
  given,
  privateThread,
  refOf,
  regenerate,
  room,
  share,
  stop,
  takeOut,
  verdictOf,
  world,
  type Row,
} from './share-world'

/**
 * Whom a share covers now: beside its consent, the agents it stays with and the earlier shares it counts on. One rule
 * (`Judge.covered`) asked by every place that shows something because it was shared.
 */

const viewer = (userId: string, more: Partial<Reader> = {}): Reader => ({ userId, orgId: ORG, member: true, ...more })
const perRecordView = async <M extends object>(rows: M[]): Promise<M[]> => rows

describe('whom a share covers', () => {
  afterEach(() => {
    resetVisibility()
  })

  for (const way of WAYS) {
    describe(`judged by ${way}`, () => {
      describe('a share that stays with agents', () => {
        // Dana's answer holds text of a skill of X's she can't open. Dana and Bob use X; Priya doesn't.
        const limited = (agents: unknown = ['X']): Row => share(answer('X says the floor price.', DANA, { direct: { records: [R2] } }), { agents })

        it('covers a person who can use the agent, and nobody else', async () => {
          const a = limited()
          const w = world([a], { agents: { X: [DANA, BOB] } })
          expect(await verdictOf(way, w, BOB, room, a)).to.equal('consented')
          expect(await verdictOf(way, w, PRIYA, room, a)).to.equal('hidden')
          expect(await verdictOf(way, w, DANA, room, a)).to.equal('visible')
        })

        it('asks for every agent: one of two is not enough', async () => {
          const a = limited(['X', 'Y'])
          const w = world([a], { agents: { X: [BOB, PRIYA], Y: [BOB] } })
          expect(await verdictOf(way, w, BOB, room, a)).to.equal('consented')
          expect(await verdictOf(way, w, PRIYA, room, a)).to.equal('hidden')
        })

        it('covers nobody while it cannot be said who may use the agent, nor when what is stored is no list of agents', async () => {
          const a = limited()
          expect(await verdictOf(way, world([a]), BOB, room, a)).to.equal('hidden')
          for (const agents of ['X', [7], [''], { X: true }]) {
            const b = limited(agents)
            expect(await verdictOf(way, world([b], { agents: { X: [BOB], '7': [BOB], '': [BOB] } }), BOB, room, b), JSON.stringify(agents)).to.equal('hidden')
          }
        })

        it('covers an answer built on it for the same people alone, also once it is an earlier version', async () => {
          const a = limited()
          const built = answer('So the price is right.', BOB, { parents: [given(a)] })
          const w = world([a, built], { agents: { X: [DANA, BOB, CAROL] } })
          expect(await verdictOf(way, w, CAROL, room, built)).to.equal('visible')
          expect(await verdictOf(way, w, PRIYA, room, built)).to.equal('hidden')
          regenerate(a, 'X says it again.', { records: [R2] })
          expect(await verdictOf(way, w, CAROL, room, built)).to.equal('visible')
          expect(await verdictOf(way, w, PRIYA, room, built)).to.equal('hidden')
          // The new version is shared with nobody.
          expect(await verdictOf(way, w, CAROL, room, a)).to.equal('hidden')
        })

        it("its own agent mark still decides who reads it whole: the share shows the text to the agent's users", async () => {
          const a = share(answer('X says.', DANA, { direct: { records: [R], agents: [{ key: 'X', kind: 'config', skill: true }] } }), { agents: ['X'] })
          const w = world([a], { agents: { X: [DANA, BOB] } })
          expect(await verdictOf(way, w, BOB, room, a)).to.equal('consented')
          // Carol can open R and not use X.
          expect(await verdictOf(way, w, CAROL, room, a)).to.equal('hidden')
        })
      })

      describe('a share that counts on an earlier one', () => {
        // Carol opens R and shares what she found. Bob can't open it; he builds on its text and shares that too.
        const chain = (): { carols: Row; bobs: Row; w: ReturnType<typeof world> } => {
          const carols = share(answer('R says CANARY.', CAROL, { direct: { records: [R] } }))
          const bobs = share(answer('Bob: CANARY.', BOB, { parents: [given(carols)] }), { on: [refOf(carols)] })
          return { carols, bobs, w: world([carols, bobs]) }
        }

        it('covers people while the earlier share stands, and nobody who cannot open it once it is stopped', async () => {
          const { carols, bobs, w } = chain()
          for (const who of [BOB, PRIYA]) expect(await verdictOf(way, w, who, room, bobs), who).to.equal('visible')
          stop(carols)
          for (const who of [BOB, PRIYA]) expect(await verdictOf(way, w, who, room, bobs), who).to.equal('hidden')
          // Dana and Carol open R themselves: the answer is theirs to read whole.
          for (const who of [DANA, CAROL]) expect(await verdictOf(way, w, who, room, bobs), who).to.equal('visible')
        })

        it('a share whose own sources only its asker opens covers the rest by its consent, while what it counts on stands', async () => {
          // Dana's answer read R2, hers alone, and was given Carol's shared text, which only Carol can open.
          const carols = share(answer('R says CANARY.', CAROL, { direct: { personal: [CAROL] } }))
          const danas = share(answer('Dana: CANARY and R2.', DANA, { direct: { records: [R2] }, parents: [given(carols)] }), { on: [refOf(carols)] })
          const w = world([carols, danas])
          expect(await verdictOf(way, w, PRIYA, room, danas)).to.equal('consented')
          stop(carols)
          for (const who of [PRIYA, BOB]) expect(await verdictOf(way, w, who, room, danas), who).to.equal('hidden')
          // What it counts on is Carol's own.
          expect(await verdictOf(way, w, CAROL, room, danas)).to.equal('consented')
        })

        it('holds for a person who opens the earlier version whole, the share gone or not', async () => {
          const carols = answer('R says CANARY.', CAROL, { direct: { records: [R] } })
          const danas = share(answer('Dana: CANARY and R2.', DANA, { direct: { records: [R2] }, parents: [given(carols)] }), { on: [refOf(carols)] })
          const w = world([carols, danas])
          // Carol opens R (the earlier answer) and not R2: the share shows her the text. Priya opens neither.
          expect(await verdictOf(way, w, CAROL, room, danas)).to.equal('consented')
          expect(await verdictOf(way, w, PRIYA, room, danas)).to.equal('hidden')
        })

        it('holds through a share that counts on another in turn, to the first one', async () => {
          const first = share(answer('R says CANARY.', CAROL, { direct: { personal: [CAROL] } }))
          const second = share(answer('Second.', DANA, { direct: { records: [R2] }, parents: [given(first)] }), { on: [refOf(first)] })
          const third = share(answer('Third.', DANA, { direct: { records: [R2] }, parents: [given(second)] }), { on: [refOf(second)] })
          const w = world([first, second, third])
          expect(await verdictOf(way, w, PRIYA, room, third)).to.equal('consented')
          stop(first)
          for (const row of [second, third]) expect(await verdictOf(way, w, PRIYA, room, row)).to.equal('hidden')
        })

        it('an earlier version keeps its share with what it counts on: a regenerated answer does not free what was built on it', async () => {
          const first = share(answer('First.', CAROL, { direct: { personal: [CAROL] } }))
          const second = share(answer('Second.', DANA, { direct: { records: [R2] }, parents: [given(first)] }), { on: [refOf(first)] })
          const built = answer('Third.', BOB, { parents: [given(second)] })
          const w = world([first, second, built])
          regenerate(second, 'Second again.', { records: [R2] })
          expect(await verdictOf(way, w, PRIYA, room, built)).to.equal('visible')
          stop(first)
          expect(await verdictOf(way, w, PRIYA, room, built)).to.equal('hidden')
          expect(await verdictOf(way, w, BOB, room, built)).to.equal('hidden')
        })

        it('a share with one person counts on another as a share with everyone does', async () => {
          const carols = share(answer('R says.', CAROL, { direct: { personal: [CAROL] } }))
          const danas = answer('Dana.', DANA, { direct: { records: [R2] }, parents: [given(carols)] })
          share(danas, { scope: 'people', on: [refOf(carols)] })
          danas.consentTo = [{ userId: BOB, memberSince: 'direct:2026-10-01T10:00:00.000Z' }]
          const w = world([carols, danas])
          expect(await verdictOf(way, w, BOB, room, danas)).to.equal('consented')
          expect(await verdictOf(way, w, PRIYA, room, danas)).to.equal('hidden')
          stop(carols)
          expect(await verdictOf(way, w, BOB, room, danas)).to.equal('hidden')
        })

        it('covers nobody when the earlier row cannot be read, or what is stored cannot', async () => {
          const bobs = share(answer('Bob.', DANA, { direct: { records: [R2] } }), { on: [{ row: 'ffffffffffffffffffffffff', ver: 'eeeeeeeeeeeeeeeeeeeeeeee' }] })
          expect(await verdictOf(way, world([bobs]), PRIYA, room, bobs)).to.equal('hidden')
          const earlier = share(answer('Earlier.', CAROL, { direct: { records: [R] } }))
          for (const on of ['all', [7], [{ row: String(earlier._id) }], [{ ver: String(earlier.labelVer) }], [{ row: String(earlier._id), ver: 3 }]]) {
            const b = share(answer('Bob.', DANA, { direct: { records: [R2] } }), { on })
            expect(await verdictOf(way, world([earlier, b]), PRIYA, room, b), JSON.stringify(on)).to.equal('hidden')
          }
        })

        it('an earlier version whose check cannot answer does not hold', async () => {
          // Carol's answer is X's and not shared: who can use X opens it.
          const carols = answer('X says.', CAROL, { direct: { agents: [{ key: 'X', kind: 'config' }] } })
          const danas = share(answer('Dana.', DANA, { direct: { personal: [DANA] } }), { on: [refOf(carols)] })
          const unsaid = world([carols, danas])
          expect(await verdictOf(way, unsaid, PRIYA, room, carols), 'control: it cannot be said').to.equal('unchecked')
          expect(await verdictOf(way, unsaid, PRIYA, room, danas)).to.equal('hidden')
          const said = world([carols, danas], { agents: { X: [PRIYA] } })
          expect(await verdictOf(way, said, PRIYA, room, danas), 'control: she can').to.equal('consented')
        })

        it('two shares that count on each other cover nobody', async () => {
          const a = share(answer('A.', DANA, { direct: { records: [R2] } }))
          const b = share(answer('B.', DANA, { direct: { records: [R2] } }), { on: [refOf(a)] })
          share(a, { on: [refOf(b)] })
          const w = world([a, b])
          for (const row of [a, b]) expect(await verdictOf(way, w, PRIYA, room, row)).to.equal('hidden')
        })

        it('an earlier share that stays with agents lends its cover only to their users', async () => {
          const first = share(answer('X says.', CAROL, { direct: { personal: [CAROL] } }), { agents: ['X'] })
          const second = share(answer('Second.', DANA, { direct: { records: [R2] }, parents: [given(first)] }), { on: [refOf(first)] })
          const w = world([first, second], { agents: { X: [CAROL, DANA, BOB] } })
          expect(await verdictOf(way, w, BOB, room, second)).to.equal('consented')
          expect(await verdictOf(way, w, PRIYA, room, second)).to.equal('hidden')
        })

        it('is for the chats the read happens in, as the share itself is', async () => {
          const { bobs, w } = chain()
          expect(await verdictOf(way, w, PRIYA, room, bobs, { chatsRead: [FORK] })).to.equal('hidden')
          const inThread = share(answer('In the thread.', DANA, { sessionId: AGENT_THREAD, direct: { records: [R2] } }), { on: [refOf(bobs)] })
          w.store.set(String(inThread._id), inThread)
          expect(await verdictOf(way, w, PRIYA, agentThread, inThread)).to.equal('consented')
        })
      })
    })
  }

  describe('what the sender\'s model is given', () => {
    const historyOf = async (who: string, w: ReturnType<typeof world>, rows: Row[]): Promise<Map<string, unknown>> => {
      const sent = await includableRows({ userId: who, orgId: ORG, member: true, identity: 'a request' }, room, rows, { deps: w.deps, member: true })
      return new Map(sent.map((r) => [String((r as Row)._id), (r as Row).content]))
    }

    it('the text of a share that stays with agents goes to the models of their users alone', async () => {
      const a = share(answer('X says the floor price.', DANA, { direct: { records: [R2] } }), { agents: ['X'] })
      const w = world([a], { agents: { X: [DANA, BOB] } })
      expect((await historyOf(BOB, w, [a])).get(String(a._id))).to.equal('X says the floor price.')
      expect((await historyOf(PRIYA, w, [a])).get(String(a._id))).to.equal('')
    })

    it('one a person can open whole is theirs to bring in by replying, not given by a share that does not cover them', async () => {
      // Carol opens R, so she reads Dana's answer whole; she can't use X, whose users its share stays with.
      const a = share(answer('X says R.', DANA, { direct: { records: [R] } }), { agents: ['X'] })
      const w = world([a], { agents: { X: [DANA, BOB] } })
      expect(await verdictOf('closures', w, CAROL, room, a)).to.equal('visible')
      expect((await historyOf(CAROL, w, [a])).get(String(a._id))).to.equal('')
      const usesX = world([a], { agents: { X: [DANA, BOB, CAROL] } })
      expect((await historyOf(CAROL, usesX, [a])).get(String(a._id))).to.equal('X says R.')
    })

    it('the text of a share that counted on one now stopped goes to no model but of those who can open it', async () => {
      const carols = share(answer('CANARY.', CAROL, { direct: { personal: [CAROL] } }))
      const danas = share(answer('Dana: CANARY.', DANA, { direct: { records: [R2] }, parents: [given(carols)] }), { on: [refOf(carols)] })
      const w = world([carols, danas])
      expect((await historyOf(PRIYA, w, [carols, danas])).get(String(danas._id))).to.equal('Dana: CANARY.')
      stop(carols)
      const after = await historyOf(PRIYA, w, [carols, danas])
      expect([...after.values()]).to.deep.equal(['', ''])
    })
  })

  describe('shareCovers: one share, one person', () => {
    const session = room
    const covers = async (w: ReturnType<typeof world>, who: string | Reader, row: Row, where = session): Promise<boolean> =>
      shareCovers(typeof who === 'string' ? viewer(who) : who, row, String(row.labelVer), { session: where, deps: w.deps })

    it('a plain share is decided with nothing read and nobody asked', async () => {
      const a = share(answer('R says.', CAROL, { direct: { records: [R] } }))
      const w = world([])
      w.deps.labels = async () => {
        throw new Error('nothing to read')
      }
      expect(await covers(w, PRIYA, a)).to.equal(true)
      expect(w.asked).to.deep.equal([])
      expect(w.agentsAsked).to.deep.equal([])
      stop(a)
      expect(await covers(w, PRIYA, a)).to.equal(false)
    })

    it('a member only, in the chat the share is for', async () => {
      const a = share(answer('R says.', CAROL, { direct: { records: [R] } }))
      const w = world([a])
      expect(await covers(w, viewer(PRIYA, { member: false }), a)).to.equal(false)
      expect(await covers(w, PRIYA, a, { ...room, _id: FORK })).to.equal(false)
      expect(await covers(w, PRIYA, a, privateThread)).to.equal(true)
    })

    it('asks the agents it stays with, on the person\'s behalf when no request of theirs is at hand', async () => {
      const a = share(answer('X says.', DANA, { direct: { records: [R2] } }), { agents: ['X'] })
      const w = world([a], { agents: { X: [DANA, BOB] } })
      expect(await covers(w, BOB, a)).to.equal(true)
      expect(await covers(w, PRIYA, a)).to.equal(false)
      expect(w.agentsAsked.every((q) => q.onBehalf)).to.equal(true)
      w.agentsAsked.length = 0
      expect(await covers(w, viewer(BOB, { identity: 'a request' }), a)).to.equal(true)
      expect(w.agentsAsked.map((q) => q.onBehalf)).to.deep.equal([false])
      // Nobody can say: covers nobody.
      expect(await covers(world([a]), BOB, a)).to.equal(false)
    })

    it('reads what it counts on from the store, and covers while that holds', async () => {
      const carols = share(answer('R says.', CAROL, { direct: { records: [R] } }))
      const bobs = share(answer('Bob.', BOB, { direct: { personal: [BOB] }, parents: [given(carols)] }), { on: [refOf(carols)] })
      const w = world([carols, bobs])
      expect(await covers(w, PRIYA, bobs)).to.equal(true)
      stop(carols)
      expect(await covers(w, PRIYA, bobs)).to.equal(false)
      // Dana opens R, so the version it counts on is hers whole.
      expect(await covers(w, DANA, bobs)).to.equal(true)
      w.store.delete(String(carols._id))
      expect(await covers(w, DANA, bobs)).to.equal(false)
    })

    it('an agent thread\'s row is covered by its thread\'s read, and by no read of the room', async () => {
      const a = share(answer('X says.', DANA, { sessionId: AGENT_THREAD, direct: { records: [R2] } }))
      const w = world([a])
      expect(await covers(w, BOB, a, agentThread)).to.equal(true)
      expect(await covers(w, BOB, a, room)).to.equal(false)
    })
  })

  describe('what a shared copy counts on', () => {
    /** Carol's answer, shared in the room; Dana's private answer built on it, shown to the room by a copy that counts on it. */
    const told = (): { carols: Row; original: Row; copy: Row; later: Row; w: ReturnType<typeof world> } => {
      const carols = share(answer('R says CANARY.', CAROL, { direct: { personal: [CAROL] } }))
      const original = answer('Dana, privately: CANARY.', DANA, { sessionId: THREAD, parents: [given(carols)] })
      const copy = copyOf(original, { on: [refOf(carols)] })
      const later = answer('Bob: CANARY.', BOB, { parents: [given(copy)] })
      return { carols, original, copy, later, w: world([carols, original, copy, later]) }
    }

    for (const way of WAYS) {
      it(`(${way}) the copy shows its text while the share stands, and hides with what was built on it when it ends`, async () => {
        const { carols, copy, later, w } = told()
        for (const who of [BOB, PRIYA, DANA]) {
          expect(await verdictOf(way, w, who, room, copy), who).to.equal('consented')
          expect(await verdictOf(way, w, who, room, later), who).to.equal('visible')
        }
        stop(carols)
        for (const who of [BOB, PRIYA, DANA]) {
          expect(await verdictOf(way, w, who, room, copy), who).to.equal('hidden')
          expect(await verdictOf(way, w, who, room, later), who).to.equal('hidden')
        }
      })
    }

    it('the stored copy says what it counts on: a row passed without it is not shown for that', async () => {
      const { carols, copy, w } = told()
      stop(carols)
      const passed = { ...copy, sharedFrom: { ...(copy.sharedFrom as Record<string, unknown>), on: undefined } }
      expect(await verdictOf('closures', w, PRIYA, room, passed)).to.equal('hidden')
    })

    it('a copy whose counted-on shares cannot be read as any hides', async () => {
      const { copy, w } = told()
      ;(copy.sharedFrom as Record<string, unknown>).on = 'all'
      expect(await verdictOf('closures', w, PRIYA, room, copy)).to.equal('hidden')
    })

    it('a reader is never given what a copy counts on, nor what one taken out was made from', async () => {
      const { copy, w } = told()
      const stored = { ...copy, removedFrom: { messageId: 'x' }, again: 'y' }
      const { rows } = await readerView({ userId: PRIYA, orgId: ORG, member: true }, room, [stored], { deps: w.deps, member: true, perRecordView })
      expect(rows[0]).to.include({ content: 'Dana, privately: CANARY.' })
      expect(JSON.stringify(rows[0])).to.not.match(/"on"|removedFrom|again/)
    })

    it('a hidden copy stays a copy: who shared it and when, with no word of it, no question, and nothing it names', async () => {
      const { carols, copy, original, w } = told()
      copy.sharedQuestion = 'What does R say?'
      stop(carols)
      const { rows } = await readerView({ userId: PRIYA, orgId: ORG, member: true }, room, [copy], { deps: w.deps, member: true, perRecordView })
      const shown = rows[0] as Record<string, unknown>
      expect(shown).to.deep.include({ messageType: 'shared_answer', sharedQuestion: '', authorUserId: DANA, content: 'This answer used content you can no longer access.' })
      expect(shown.answerHidden).to.deep.include({ redacted: true })
      expect(shown.sharedFrom).to.have.all.keys('sharedBy', 'sharedAt')
      expect(JSON.stringify(shown)).to.not.match(new RegExp(`CANARY|What does R|${String(original._id)}|${String(carols._id)}`))
    })
  })

  describe('the message of an agent thread\'s answer that counts on an earlier share of that thread', () => {
    /**
     * Carol's answer in X's thread, shared with everyone; Dana's there, built on its text and shared with everyone too, so
     * a message shows it to the room. Bob uses X and can open neither; Priya doesn't use X, and sees the room alone.
     */
    const inThread = (more: Record<string, unknown> = {}): { first: Row; second: Row; message: Row; later: Row; w: ReturnType<typeof world> } => {
      const first = share(answer('X to Carol: CANARY.', CAROL, { sessionId: AGENT_THREAD, direct: { personal: [CAROL] } }), more)
      const second = share(answer('X to Dana: CANARY.', DANA, { sessionId: AGENT_THREAD, direct: { personal: [DANA] }, parents: [given(first)] }), { on: [refOf(first)] })
      const message = copyOf(second, { fromAgentThread: true, on: [refOf(first)] })
      const later = answer('Bob, in the room: CANARY.', BOB, { parents: [given(message)] })
      return { first, second, message, later, w: world([first, second, message, later], { agents: { X: [CAROL, DANA, BOB] } }) }
    }

    for (const way of WAYS) {
      it(`(${way}) the room reads it while that share stands, who can't use the agent too, and not after`, async () => {
        const { first, message, later, w } = inThread()
        for (const who of [PRIYA, BOB, CAROL]) {
          expect(await verdictOf(way, w, who, room, message), who).to.equal('consented')
          expect(await verdictOf(way, w, who, room, later), who).to.equal('visible')
        }
        stop(first)
        for (const who of [PRIYA, BOB]) {
          expect(await verdictOf(way, w, who, room, message), who).to.equal('hidden')
          expect(await verdictOf(way, w, who, room, later), who).to.equal('hidden')
        }
        // What it counts on is Carol's own, so it holds for her.
        expect(await verdictOf(way, w, CAROL, room, message)).to.equal('consented')
      })

      it(`(${way}) a share of the thread itself is never the room's to read by that, nor is one that stays with its agent's users`, async () => {
        const { first, second, w } = inThread()
        for (const row of [first, second]) expect(await verdictOf(way, w, PRIYA, room, row)).to.equal('hidden')
        const built = answer('In the room.', BOB, { parents: [given(second)] })
        w.store.set(String(built._id), built)
        expect(await verdictOf(way, w, PRIYA, room, built)).to.equal('hidden')
        const limited = inThread({ agents: ['X'] })
        expect(await verdictOf(way, limited.w, PRIYA, room, limited.message)).to.equal('hidden')
      })

      it(`(${way}) a share of another thread is counted on by no message of this one`, async () => {
        const { first, message, w } = inThread()
        first.sessionId = FORK
        expect(await verdictOf(way, w, PRIYA, room, message)).to.equal('hidden')
      })
    }

    it('a private answer\'s message counts on nothing of its private thread, whose rows nobody else reads', async () => {
      for (const way of WAYS) {
        const first = share(answer('Dana, privately: CANARY.', DANA, { sessionId: THREAD, direct: { personal: [DANA] } }))
        const second = answer('Dana, privately again.', DANA, { sessionId: THREAD, direct: { personal: [DANA] }, parents: [given(first)] })
        const message = copyOf(second, { on: [refOf(first)] })
        const w = world([first, second, message])
        expect(await verdictOf(way, w, PRIYA, room, message), way).to.equal('hidden')
      }
    })

    it('and it can be counted on in the room while it stands', async () => {
      const { first, message, later, w } = inThread()
      const widen = () => widenOK(later, String(later.labelVer), 'everyone', { session: room, deps: w.deps })
      expect(await widen()).to.deep.equal({ ok: true, on: [refOf(message)] })
      stop(first)
      expect(await widen()).to.deep.equal({ ok: false, code: 'SHARE_NOT_WIDENABLE' })
    })
  })

  it('a share someone may read whole is never narrowed by it: fullOK asks nothing of shares', async () => {
    const a = share(answer('R says.', DANA, { direct: { records: [R] } }), { agents: ['X'], on: [{ row: 'gone', ver: 'gone' }] })
    const w = world([a])
    expect(await fullOK(viewer(CAROL), a, String(a.labelVer), { session: room, deps: w.deps })).to.equal(true)
    expect(consentCovers(a, String(a.labelVer), viewer(PRIYA), new Set([ROOM])), 'the consent alone').to.equal(true)
    expect(ON_BEHALF_OF_READER.description).to.equal('on behalf of the reader')
  })

  it('a copy taken out and what it counts on: only the live one is asked what it counts on', async () => {
    const carols = share(answer('R says.', CAROL, { direct: { personal: [CAROL] } }))
    const original = answer('Dana, privately.', DANA, { sessionId: THREAD, direct: { personal: [DANA] }, parents: [given(carols)] })
    const copy = copyOf(original, { on: [refOf(carols)] })
    const bobs = answer('Bob.', BOB, { parents: [given(copy)] })
    const second = copyOf(original, { on: [refOf(carols)] })
    takeOut(copy, { again: second })
    const w = world([carols, original, copy, second, bobs])
    expect(await verdictOf('closures', w, PRIYA, room, bobs)).to.equal('visible')
    stop(carols)
    expect(await verdictOf('closures', w, PRIYA, room, bobs)).to.equal('hidden')
  })
})
