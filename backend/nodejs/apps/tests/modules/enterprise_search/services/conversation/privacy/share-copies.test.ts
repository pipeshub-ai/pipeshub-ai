import { expect } from 'chai'
import {
  includableRows,
  resetVisibility,
  visibleRows,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import {
  AGENT_THREAD,
  BOB,
  BOBS_THREAD,
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
  ZED,
  agentThread,
  answer,
  copyOf,
  given,
  nextId,
  privateThread,
  regenerate,
  room,
  takeOut,
  verdictOf,
  verdicts,
  world,
  type Row,
  type WorldOptions,
} from './share-world'

/**
 * A shared copy (the message a private or agent thread's answer is shown to its room by) as something later answers
 * were given: what it lends while it is in the chat, outside that chat, and once it was taken out.
 */

const fork = { _id: FORK, orgId: ORG, userId: CAROL, sharedWith: [{ userId: ZED, accessLevel: 'read', addedAt: new Date('2026-10-05T10:00:00Z') }] }

interface Told {
  original: Row
  copy: Row
  later: Row
  w: ReturnType<typeof world>
}

/**
 * An agent's answer to Dana, in its thread of the room, read R (she and Carol can open it); Bob, who can't, asked about
 * its copy in the room. Dana, Carol and Bob use the agent, Priya doesn't; all four are the room's people.
 */
const told = (direct: Record<string, unknown[]> = { records: [R] }, opts: WorldOptions = {}): Told => {
  const original = answer('X to Dana: CANARY.', DANA, { sessionId: AGENT_THREAD, direct })
  const copy = copyOf(original, { fromAgentThread: true })
  const later = answer('Bob: CANARY.', BOB, { parents: [given(copy)] })
  return { original, copy, later, w: world([original, copy, later], { agents: { X: [DANA, CAROL, BOB] }, chats: { [ROOM]: [DANA, CAROL, BOB, PRIYA] }, ...opts }) }
}

/** The same of a private answer of Dana's. */
const toldPrivately = (direct: Record<string, unknown[]> = { records: [R] }): Told => {
  const original = answer('Dana, privately: CANARY.', DANA, { sessionId: THREAD, direct })
  const copy = copyOf(original)
  const later = answer('Bob: CANARY.', BOB, { parents: [given(copy)] })
  return { original, copy, later, w: world([original, copy, later], { agents: { X: [DANA, CAROL, BOB] }, chats: { [ROOM]: [DANA, CAROL, BOB, PRIYA] } }) }
}

describe('a shared copy that later answers were given', () => {
  afterEach(() => {
    resetVisibility()
  })

  for (const way of WAYS) {
    describe(`judged by ${way}`, () => {
      it('while it is in the chat, every member reads an answer built on it, in the room and in its threads', async () => {
        for (const { later, w } of [told(), toldPrivately()]) {
          for (const who of [BOB, PRIYA, CAROL, DANA]) expect(await verdictOf(way, w, who, room, later), who).to.equal('visible')
          const inThread = answer('In the agent thread: CANARY.', BOB, { sessionId: AGENT_THREAD, parents: [given(later)] })
          w.store.set(String(inThread._id), inThread)
          expect(await verdictOf(way, w, PRIYA, agentThread, inThread)).to.equal('visible')
        }
      })

      it('in another chat it is the answer it was made from: a fork shows nobody what reached its owner through it', async () => {
        const { later, w } = told()
        const forked = { ...later, sessionId: FORK }
        // The room's reader who can open nothing, read as the fork reads; and the fork's guest.
        expect(await verdictOf(way, w, BOB, room, later, { chatsRead: [FORK] })).to.equal('hidden')
        w.store.set(String(forked._id), forked)
        expect(await verdictOf(way, w, ZED, fork, forked)).to.equal('hidden')
        // Carol reads the thread and can open what the answer used: what was built on it is hers to take along.
        expect(await verdictOf(way, w, CAROL, room, { ...later }, { chatsRead: [FORK] })).to.equal('visible')
        expect(await verdictOf(way, w, CAROL, fork, forked)).to.equal('visible')
      })

      it('taken out, it is the answer it was made from: who can open that keeps what was built on it, its askers too', async () => {
        const { copy, later, w } = told()
        takeOut(copy)
        for (const who of [BOB, PRIYA]) expect(await verdictOf(way, w, who, room, later), who).to.equal('hidden')
        for (const who of [CAROL, DANA]) expect(await verdictOf(way, w, who, room, later), who).to.equal('visible')
      })

      it("of an agent's thread, it is for who reads that thread, also when it used nothing: the room's people who can use the agent", async () => {
        const { copy, later, w } = told({}, { agents: { X: [DANA, CAROL, BOB, ZED] } })
        const forked = { ...later, sessionId: FORK }
        w.store.set(String(forked._id), forked)
        // Zed can use the agent and is not in the room; Carol, whose fork it is, is.
        expect(await verdictOf(way, w, ZED, fork, forked)).to.equal('hidden')
        expect(await verdictOf(way, w, CAROL, fork, forked)).to.equal('visible')
        w.store.set(String(later._id), later)
        takeOut(copy)
        for (const who of [DANA, CAROL, BOB]) expect(await verdictOf(way, w, who, room, later), who).to.equal('visible')
        expect(await verdictOf(way, w, PRIYA, room, later)).to.equal('hidden')
      })

      it("of a private thread, it is its asker's alone, whoever can open what it used, and also when it used nothing", async () => {
        const directs: Array<Record<string, unknown[]>> = [{ records: [R] }, {}]
        for (const direct of directs) {
          const { copy, later, w } = toldPrivately(direct)
          const inFork = { ...fork, sharedWith: [{ userId: DANA, accessLevel: 'read', addedAt: new Date('2026-10-05T10:00:00Z') }] }
          const forked = { ...later, sessionId: FORK }
          w.store.set(String(forked._id), forked)
          for (const who of [CAROL, BOB]) expect(await verdictOf(way, w, who, inFork, forked), `${who} in the fork`).to.equal('hidden')
          expect(await verdictOf(way, w, DANA, inFork, forked)).to.equal('visible')
          w.store.set(String(later._id), later)
          takeOut(copy)
          for (const who of [CAROL, BOB, PRIYA]) expect(await verdictOf(way, w, who, room, later), who).to.equal('hidden')
          expect(await verdictOf(way, w, DANA, room, later)).to.equal('visible')
        }
      })

      it('what cannot be said of its thread leaves what was built on it unchecked; a thread that is gone hides it', async () => {
        const unread = told({})
        takeOut(unread.copy)
        unread.w.deps.chats = async () => {
          throw new Error('down')
        }
        expect(await verdictOf(way, unread.w, CAROL, room, unread.later)).to.equal('unchecked')
        const unsaid = told({}, { agents: {} })
        takeOut(unsaid.copy)
        expect(await verdictOf(way, unsaid.w, CAROL, room, unsaid.later)).to.equal('unchecked')
        const elsewhere = told({}, { chats: {} })
        expect(await verdictOf(way, elsewhere.w, CAROL, room, elsewhere.later, { chatsRead: [FORK] })).to.equal('unchecked')
        for (const gone of [told({}), toldPrivately({})]) {
          takeOut(gone.copy)
          gone.w.deps.chats = async () => new Map()
          for (const who of [CAROL, DANA]) expect(await verdictOf(way, gone.w, who, room, gone.later), who).to.equal('hidden')
        }
      })

      it('taken out by an older build, which kept nothing of its answer, it passes for nobody', async () => {
        const { copy, later, w } = told()
        takeOut(copy, { kept: false })
        for (const who of [BOB, CAROL, DANA]) expect(await verdictOf(way, w, who, room, later), who).to.equal('hidden')
      })

      it('shared again, the new copy stands for it: what it hid comes back for everyone, and goes again with the new one', async () => {
        const { original, copy, later, w } = told()
        const second = copyOf(original)
        w.store.set(String(second._id), second)
        takeOut(copy, { again: second })
        for (const who of [BOB, PRIYA, CAROL]) expect(await verdictOf(way, w, who, room, later), who).to.equal('visible')
        takeOut(second)
        for (const who of [BOB, PRIYA]) expect(await verdictOf(way, w, who, room, later), who).to.equal('hidden')
        expect(await verdictOf(way, w, CAROL, room, later)).to.equal('visible')
        const third = copyOf(original)
        w.store.set(String(third._id), third)
        second.again = third._id
        // Two copies on: followed to the end.
        for (const who of [BOB, PRIYA]) expect(await verdictOf(way, w, who, room, later), who).to.equal('visible')
      })

      it('replaced by a newer version, the newer message stands for it; once that is out too, the version given decides', async () => {
        const { original, copy, later, w } = told({ records: [R2] })
        const first = { ...original }
        regenerate(original, 'X to Dana: nothing in it.', {})
        expect(first.labelVer).to.not.equal(original.labelVer)
        const second = copyOf(original)
        w.store.set(String(second._id), second)
        takeOut(copy, { again: second })
        expect(await verdictOf(way, w, PRIYA, room, later)).to.equal('visible')
        takeOut(second)
        // The newer version used nothing, but what Bob's model was given is the first one's text: R2, which only Dana opens.
        for (const who of [PRIYA, BOB, CAROL]) expect(await verdictOf(way, w, who, room, later), who).to.equal('hidden')
        expect(await verdictOf(way, w, DANA, room, later)).to.equal('visible')
      })

      it('a newer version stopped and shared again brings back what was built on its message, not on the one it replaced', async () => {
        const { original, copy, later, w } = told()
        const first = { ...original }
        regenerate(original, 'X to Dana: the same source, written again.', { records: [R] })
        expect(first.labelVer).to.not.equal(original.labelVer)
        const second = copyOf(original)
        const onSecond = answer('Bob, on the newer one.', BOB, { parents: [given(second)] })
        for (const row of [second, onSecond]) w.store.set(String(row._id), row)
        takeOut(copy, { again: second })
        for (const row of [later, onSecond]) expect(await verdictOf(way, w, PRIYA, room, row)).to.equal('visible')
        // Stopped, and the newer version shared again.
        takeOut(second)
        for (const row of [later, onSecond]) expect(await verdictOf(way, w, PRIYA, room, row)).to.equal('hidden')
        const third = copyOf(original)
        w.store.set(String(third._id), third)
        second.again = third._id
        for (const who of [BOB, PRIYA]) {
          expect(await verdictOf(way, w, who, room, onSecond), who).to.equal('visible')
          expect(await verdictOf(way, w, who, room, later), who).to.equal('hidden')
        }
      })

      it('copies that name each other, or a copy that is none of this answer or chat, stand for nothing', async () => {
        const { original, copy, later, w } = told()
        const second = copyOf(original)
        w.store.set(String(second._id), second)
        takeOut(copy, { again: second })
        takeOut(second, { again: copy })
        expect(await verdictOf(way, w, PRIYA, room, later), 'a loop').to.equal('hidden')

        const other = answer('Another answer of the thread.', DANA, { sessionId: AGENT_THREAD })
        const unrelated = copyOf(other)
        const elsewhere = { ...copyOf(original), sessionId: FORK }
        const gone = { _id: nextId() } as Row
        for (const row of [other, unrelated, elsewhere]) w.store.set(String(row._id), row)
        for (const [what, again] of [['another answer\'s copy', unrelated], ['a copy in another chat', elsewhere], ['a row that is gone', gone], ['an answer', original]] as const) {
          copy.again = again._id
          expect(await verdictOf(way, w, PRIYA, room, later), what).to.equal('hidden')
          expect(await verdictOf(way, w, CAROL, room, later), `${what}: the original still decides`).to.equal('visible')
        }
      })

      it('its answer is judged by its label alone, at the version copied: what cannot be relied on is its asker\'s', async () => {
        const { original, copy, later, w } = told()
        takeOut(copy)
        // The stored text was changed behind the label: the label of the version copied still says what it used.
        original.content = 'Changed behind its label.'
        expect(await verdictOf(way, w, CAROL, room, later)).to.equal('visible')
        ;(original.provenance as { complete: boolean }).complete = false
        expect(await verdictOf(way, w, CAROL, room, later)).to.equal('hidden')
        expect(await verdictOf(way, w, DANA, room, later)).to.equal('visible')
      })

      it('an answer that cannot be read, or a version that cannot be told, passes for nobody', async () => {
        for (const spoil of [
          (w: ReturnType<typeof world>, original: Row) => w.store.delete(String(original._id)),
          (_w: ReturnType<typeof world>, _o: Row, copy: Row) => {
            ;(copy.removedFrom as Record<string, unknown>).ver = undefined
          },
          (_w: ReturnType<typeof world>, _o: Row, copy: Row) => {
            ;(copy.removedFrom as Record<string, unknown>).ver = 'another-version'
          },
          (_w: ReturnType<typeof world>, _o: Row, copy: Row) => {
            ;(copy.removedFrom as Record<string, unknown>).messageId = copy._id
          },
        ]) {
          const { original, copy, later, w } = told()
          takeOut(copy)
          spoil(w, original, copy)
          expect(await verdictOf(way, w, CAROL, room, later)).to.equal('hidden')
        }
      })

      it("a check of the original that cannot be settled leaves what was built on it unchecked, not shown", async () => {
        const { copy, later, w } = told({ agents: [{ key: 'Y', kind: 'config' }] })
        takeOut(copy)
        expect(await verdictOf(way, w, CAROL, room, later)).to.equal('unchecked')
      })

      it('an answer from before copies kept their version passes in its chat, and in no other', async () => {
        const { original, copy, later, w } = told()
        delete (copy.sharedFrom as Record<string, unknown>).ver
        expect(await verdictOf(way, w, PRIYA, room, later)).to.equal('visible')
        expect(await verdictOf(way, w, CAROL, room, later, { chatsRead: [FORK] })).to.equal('hidden')
        expect(original.sessionId).to.equal(AGENT_THREAD)
      })
    })
  }

  it('a read of several chats at once takes a copy where it is, and never one of another chat in its place', async () => {
    // A search of one's own chats reads with no one chat: a share there counts wherever it was given.
    const anyChat = { ...room, _id: undefined }
    const { original, copy, later, w } = told()
    expect(await verdictOf('closures', w, PRIYA, anyChat, later)).to.equal('visible')
    const elsewhere = { ...copyOf(original), sessionId: FORK }
    w.store.set(String(elsewhere._id), elsewhere)
    takeOut(copy, { again: elsewhere })
    for (const way of WAYS) expect(await verdictOf(way, w, PRIYA, anyChat, later), way).to.equal('hidden')
  })

  it('a copy given whole (not as text) is judged as one given as text', async () => {
    for (const way of WAYS) {
      const { copy, w } = told()
      const whole = answer('Bob: CANARY.', BOB, { parents: [given(copy, 'full')] })
      w.store.set(String(whole._id), whole)
      expect(await verdictOf(way, w, PRIYA, room, whole), way).to.equal('visible')
      expect(await verdictOf(way, w, PRIYA, room, whole, { chatsRead: [FORK] }), way).to.equal('hidden')
      takeOut(copy)
      expect(await verdictOf(way, w, PRIYA, room, whole), way).to.equal('hidden')
      expect(await verdictOf(way, w, CAROL, room, whole), way).to.equal('visible')
    }
  })

  it('several answers on one page share what is read of the copy, and each answer further on is judged through it', async () => {
    for (const way of WAYS) {
      const { copy, later, w } = told()
      const next = answer('Carol: so be it.', CAROL, { parents: [given(later)] })
      w.store.set(String(next._id), next)
      takeOut(copy)
      const priya = await verdicts(way, w, PRIYA, room, [later, next])
      expect([priya.get(String(later._id)), priya.get(String(next._id))], way).to.deep.equal(['hidden', 'hidden'])
      const carol = await verdicts(way, w, CAROL, room, [later, next])
      expect([carol.get(String(later._id)), carol.get(String(next._id))], way).to.deep.equal(['visible', 'visible'])
    }
  })

  it('a copy that was made from an answer built on another copy is followed through it', async () => {
    for (const way of WAYS) {
      const { copy, w } = told()
      const second = answer('X to Bob: CANARY.', BOB, { sessionId: AGENT_THREAD, parents: [given(copy)] })
      const copy2 = copyOf(second, { fromAgentThread: true })
      const last = answer('Priya: CANARY.', PRIYA, { parents: [given(copy2)] })
      for (const row of [second, copy2, last]) w.store.set(String(row._id), row)
      takeOut(copy2)
      // The first copy is still in the room: the second's answer, in a thread Bob reads, is built on what every member reads.
      expect(await verdictOf(way, w, BOB, room, last), way).to.equal('visible')
      expect(await verdictOf(way, w, PRIYA, room, last), way).to.equal('hidden')
      takeOut(copy)
      expect(await verdictOf(way, w, BOB, room, last), way).to.equal('hidden')
      expect(await verdictOf(way, w, CAROL, room, last), way).to.equal('visible')
    }
  })

  it('the copy itself is shown as ever: to the room\'s members while it is there', async () => {
    const { copy, w } = told()
    for (const way of WAYS) {
      for (const who of [BOB, PRIYA, DANA]) expect(await verdictOf(way, w, who, room, copy), `${way} ${who}`).to.equal('consented')
    }
    const stranger = await visibleRows({ userId: ZED, orgId: ORG }, room, [copy], { deps: w.deps })
    expect(stranger.get(String(copy._id))?.verdict).to.equal('hidden')
    takeOut(copy)
    const out = await visibleRows({ userId: PRIYA, orgId: ORG }, room, [copy], { deps: w.deps, member: true })
    expect(out.get(String(copy._id))?.verdict).to.equal('hidden')
  })

  it('a live copy costs a read of its room nothing more: its answer is not read, and nobody is asked about it', async () => {
    const { original, later, w } = told()
    const read: string[] = []
    const labels = w.deps.labels
    w.deps.labels = async (org, ids) => {
      read.push(...ids)
      return labels ? labels(org, ids) : new Map()
    }
    expect(await verdictOf('closures', w, PRIYA, room, later)).to.equal('visible')
    expect(read).to.not.include(String(original._id))
    expect(w.asked).to.deep.equal([])
    expect(w.chatsAsked).to.deep.equal([])
  })

  it('a stored verdict of an answer built on a copy taken out is not served once the room\'s audience moved on', async () => {
    const { original, copy, later, w } = told()
    const audience = new Map<string, number>()
    const kept = { epochs: { values: async (keys: readonly string[]) => keys.map(() => '1') }, audiences: async (_org: string, ids: readonly string[]) => new Map(ids.map((id) => [id, audience.get(id) ?? 0])) }
    const read = async (): Promise<string | undefined> =>
      (await visibleRows({ userId: PRIYA, orgId: ORG }, room, [later], { deps: { ...w.deps, ...kept }, member: true })).get(String(later._id))?.verdict
    expect(await read()).to.equal('visible')
    takeOut(copy)
    audience.set(ROOM, 1)
    expect(await read()).to.equal('hidden')
    const second = copyOf(original)
    w.store.set(String(second._id), second)
    copy.again = second._id
    audience.set(ROOM, 2)
    expect(await read()).to.equal('visible')
  })

  it("the history of a fork's turn holds nothing that came through a copy of the room", async () => {
    const { copy, later, w } = told()
    const inFork = { ...later, sessionId: FORK }
    w.store.set(String(inFork._id), inFork)
    const sent = await includableRows({ userId: ZED, orgId: ORG, member: true }, fork, [inFork], { deps: w.deps, member: true, replyTargetId: String(inFork._id) })
    expect((sent[0] as Row).content).to.equal('')
    expect(copy.sessionId).to.equal(ROOM)
  })

  it('hidden from its sender, it is given to the model without its question too', async () => {
    const { original, copy, w } = told()
    copy.sharedQuestion = 'What does the QUESTION-CANARY deck say?'
    const told2 = { ...copy, sharedFrom: { ...(copy.sharedFrom as object), on: [{ row: String(original._id), ver: 'gone' }] } }
    w.store.set(String(told2._id), told2)
    const [shown, hidden] = await Promise.all(
      [copy, told2].map((row) => includableRows({ userId: PRIYA, orgId: ORG, member: true }, room, [row], { deps: { ...w.deps, labels: async () => new Map([[String(row._id), row], [String(original._id), original]]) }, member: true, replyTargetId: String(row._id) })),
    )
    expect(JSON.stringify(shown), 'control').to.include('QUESTION-CANARY')
    expect(JSON.stringify(hidden)).to.not.include('CANARY')
  })

  it('in a private thread of the room, the thread\'s own answer built on the copy is its asker\'s while the copy is there', async () => {
    const { copy, w } = told()
    const bobsThread = { ...privateThread, _id: BOBS_THREAD }
    const own = answer('Bob, privately.', BOB, { sessionId: BOBS_THREAD, parents: [given(copy)] })
    w.store.set(String(own._id), own)
    for (const way of WAYS) expect(await verdictOf(way, w, BOB, bobsThread, own), way).to.equal('visible')
    takeOut(copy)
    for (const way of WAYS) expect(await verdictOf(way, w, BOB, bobsThread, own), way).to.equal('hidden')
  })

  it("in the thread it was made in, the thread's own answers built on it are judged by the answer itself", async () => {
    const { copy, w } = told({})
    const again = answer('X to Bob: CANARY.', BOB, { sessionId: AGENT_THREAD, parents: [given(copy)] })
    w.store.set(String(again._id), again)
    const privately = toldPrivately()
    const own = answer('Dana, privately again.', DANA, { sessionId: THREAD, parents: [given(privately.copy)] })
    privately.w.store.set(String(own._id), own)
    for (const c of [copy, privately.copy]) takeOut(c)
    for (const way of WAYS) {
      for (const who of [DANA, CAROL, BOB]) expect(await verdictOf(way, w, who, agentThread, again), `${way} ${who}`).to.equal('visible')
      expect(await verdictOf(way, privately.w, DANA, privateThread, own), way).to.equal('visible')
    }
  })
})
