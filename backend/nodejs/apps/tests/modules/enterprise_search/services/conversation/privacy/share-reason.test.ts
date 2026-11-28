import { expect } from 'chai'
import sinon from 'sinon'
import { Logger } from '../../../../../../src/libs/services/logger.service'
import {
  configureVisibility,
  rememberAnswerClosures,
  resetVisibility,
  whyNotWidenable,
  widenOK,
  type VisibilityDeps,
  type WidenAudience,
  type WidenResult,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { memoryClosureStore } from './closure-world'
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
  T1,
  THREAD,
  WAYS,
  agentThread,
  answer,
  given,
  nextId,
  privateThread,
  refOf,
  room,
  share,
  unreadShares,
  verdictOf,
  world,
  type Row,
  type WorldOptions,
} from './share-world'
import type { ReaderSession } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'

/**
 * Why a share `widenOK` refused as not widenable can't be made, as its asker is told it (`whyNotWidenable`): the reason
 * itself, never the one sentence for all of them. It is asked only after the refusal and never makes a share.
 */

const NOT_WIDENABLE = { ok: false, code: 'SHARE_NOT_WIDENABLE' }
const NOT_CHECKED = { ok: false, code: 'SHARE_NOT_CHECKED' }

const why = async (rows: Row[], row: Row, session: ReaderSession = room, opts: WorldOptions = {}) => {
  resetVisibility()
  const w = world(rows, opts)
  const widened = await widenOK(row, String(row.labelVer), 'everyone', { session, deps: w.deps })
  expect(widened, 'refused first').to.deep.equal(NOT_WIDENABLE)
  resetVisibility()
  return whyNotWidenable(row, String(row.labelVer), { session, deps: w.deps })
}

/**
 * The share check of `row` as it decides by the closures it makes, by closures kept since the writes, and by the walk
 * (its share marks can't be read): all three must say the same. `byClosures`: the closures it made decided, so the
 * walk never read the answer again.
 */
const widenEachWay = async (
  rows: Row[],
  row: Row,
  session: ReaderSession | undefined = room,
  audience: WidenAudience = 'everyone',
  opts: WorldOptions = {},
): Promise<{ result: WidenResult; byClosures: boolean }> => {
  const ver = String(row.labelVer)
  const w = world(rows, opts)
  const labels = w.deps.labels as VisibilityDeps['labels']
  let reads = 0
  resetVisibility()
  const result = await widenOK(row, ver, audience, {
    session,
    deps: {
      ...w.deps,
      labels: async (org, ids) => {
        if (ids.includes(String(row._id))) reads += 1
        return labels(org, ids)
      },
    },
  })
  const { met, closures: unread } = unreadShares()
  // The store that can't give share marks says so, and that it answers again when it gives anything else.
  const quiet = [sinon.stub(Logger.prototype, 'warn'), sinon.stub(Logger.prototype, 'info')]
  try {
    resetVisibility()
    const walked = await widenOK(row, ver, audience, { session, deps: { ...w.deps, closures: unread } })
    expect(walked, 'the walk says the same').to.deep.equal(result)
  } finally {
    for (const stub of quiet) stub.restore()
  }
  expect(met.shares, 'the walk decided: no share mark could be read').to.be.greaterThan(0)
  resetVisibility()
  const closures = memoryClosureStore(w.store) as unknown as VisibilityDeps['closures']
  configureVisibility({ ...w.deps, closures })
  await rememberAnswerClosures(ORG, [...w.store.values()])
  resetVisibility()
  const kept = await widenOK(row, ver, audience, { session, deps: { ...w.deps, closures } })
  expect(kept, 'kept closures say the same').to.deep.equal(result)
  resetVisibility()
  return { result, byClosures: reads === 1 }
}

/** A person's message, in the room unless `more` says where: it carries no label. */
const message = (messageType: 'note' | 'user_query', by: string, content: string, more: Record<string, unknown> = {}): Row => ({
  _id: nextId(),
  sessionId: ROOM,
  orgId: ORG,
  seq: 1,
  messageType,
  authorUserId: by,
  content,
  createdAt: T1,
  ...more,
})

/** The agent of the room's agent thread, which everyone in the room can use. */
const AGENT = { key: 'X', kind: 'config' }
const everyoneUsesIt: WorldOptions = { agents: { X: [DANA, CAROL, BOB, PRIYA] } }

/**
 * A chat as saas-test held it (room 6b06db32): two answers of Dana's that used her own tools, the second built on the
 * first; her note; her question replying to the note, whose answer (6b0831a9) was given the note at `legacy` (a note
 * never has a version of its own); and two answers of the room's agent thread (6b083c99), the second replying to the
 * first, each given every earlier answer of hers.
 */
const replyToANote = () => {
  const first = answer('First.', DANA, { direct: { personal: [DANA] } })
  const second = answer('Second.', DANA, { direct: { personal: [DANA] }, parents: [given(first)] })
  const note = message('note', DANA, 'A note.')
  const replied = answer('Replied.', DANA, { parents: [given(first), given(second), given(note)] })
  const inThread = { sessionId: AGENT_THREAD, direct: { agents: [AGENT] } }
  const a1 = answer('Thread one.', DANA, { ...inThread, parents: [given(first), given(second), given(replied)] })
  const a2 = answer('Got it.', DANA, {
    ...inThread,
    parents: [given(first), given(second), given(replied), given(a1, 'full'), given(a1)],
  })
  return { first, second, note, replied, a1, a2, rows: [first, second, note, replied, a1, a2] }
}

/**
 * The other chat saas-test held (room 6b087998): an answer that used R; one given it in full and a note as text
 * (d6f2c5); and one given the first in full and that one as text (d6f2c7), so the note is behind an answer behind it.
 */
const behindAnAnswer = () => {
  const fromR = answer('From R.', DANA, { direct: { records: [R] } })
  const note = message('note', DANA, 'A note.')
  const onNote = answer('On the note.', DANA, { parents: [given(fromR, 'full'), given(note)] })
  const onThat = answer('On that.', DANA, { parents: [given(fromR, 'full'), given(onNote)] })
  return { fromR, note, onNote, onThat, rows: [fromR, note, onNote, onThat] }
}

describe('why a share is refused', () => {
  afterEach(() => {
    resetVisibility()
  })

  it('built on an answer someone shared only with the asker: that, and who shared it', async () => {
    const withBob = share(answer('R2 says.', DANA, { direct: { records: [R2] } }), { scope: 'people' })
    withBob.consentTo = [{ userId: BOB, memberSince: 'direct:2026-10-01T10:00:00.000Z' }]
    const bobs = answer('Two poems.', BOB, { parents: [given(withBob)] })
    expect(await why([withBob, bobs], bobs)).to.deep.equal({ ok: false, code: 'SHARE_BUILT_ON_SHARED', by: DANA })
  })

  it('no label, its own or behind it: made before the checks', async () => {
    const old = answer('Before labels.', BOB)
    delete old.provenance
    const built = answer('Built.', BOB, { parents: [given(old)] })
    expect(await why([old, built], built)).to.deep.equal({ ok: false, code: 'SHARE_UNLABELLED' })
    const own = answer('Before labels too.', BOB)
    delete own.provenance
    expect(await why([own], own)).to.deep.equal({ ok: false, code: 'SHARE_UNLABELLED' })
  })

  it('an answer the previous release wrote, with no asker on it: made before the checks', async () => {
    const before = answer('From the previous release.', BOB)
    delete before.provenance
    delete before.requestedBy
    expect(await why([before], before)).to.deep.equal({ ok: false, code: 'SHARE_UNLABELLED' })
  })

  describe("built on a person's message, which never carries a label", () => {
    describe('one of the room the share lands in, there now and answering no card: every member reads it, so it stops nothing', () => {
      it("the room's answer that replied to a note, and its agent thread's answers built on that one (saas-test 6b06db32)", async () => {
        const { rows, replied, a1, a2 } = replyToANote()
        const inRoom = await widenEachWay(rows, replied)
        expect(inRoom.result).to.deep.equal({ ok: true })
        expect(inRoom.byClosures, 'decided by its closures').to.equal(true)
        for (const [label, row] of [['the first', a1], ['the reply to it', a2]] as const) {
          const inThread = await widenEachWay(rows, row, agentThread, 'everyone', everyoneUsesIt)
          expect(inThread.result, label).to.deep.equal({ ok: true })
          expect(inThread.byClosures, `${label}: decided by the walk`).to.equal(false)
        }
      })

      it('shared with people as with everyone: those who still cannot open what it used are named, as ever', async () => {
        const { rows, replied, a1 } = replyToANote()
        for (const [label, row, session] of [['in the room', replied, room], ['in the thread', a1, agentThread]] as const) {
          const { result } = await widenEachWay(rows, row, session, { userIds: [CAROL] }, everyoneUsesIt)
          // Carol can't open what Dana's own tools gave the earlier answers: the dialog says so, and the share is made.
          expect(result, label).to.deep.equal({ ok: true, cannotOpen: [CAROL] })
        }
      })

      it('an answer behind an answer behind the note, and the one given it, are shared (saas-test 6b087998)', async () => {
        const { rows, onNote, onThat } = behindAnAnswer()
        for (const [label, row] of [['given the note', onNote], ['given that answer', onThat]] as const) {
          const everyone = await widenEachWay(rows, row)
          expect(everyone.result, label).to.deep.equal({ ok: true })
          expect(everyone.byClosures, `${label}: decided by its closures`).to.equal(true)
          // Carol opens R as Dana does; Bob doesn't, and is named.
          expect((await widenEachWay(rows, row, room, { userIds: [CAROL] })).result, `${label}: Carol`).to.deep.equal({ ok: true })
          expect((await widenEachWay(rows, row, room, { userIds: [BOB] })).result, `${label}: Bob`).to.deep.equal({ ok: true, cannotOpen: [BOB] })
        }
      })

      it("a question of the room's, by anyone, is the same", async () => {
        const first = answer('First.', DANA)
        const question = message('user_query', BOB, 'A question.')
        const replied = answer('Replied.', DANA, { parents: [given(first), given(question)] })
        expect((await widenEachWay([first, question, replied], replied)).result).to.deep.equal({ ok: true })
      })

      it("a private thread's answer that replied to a note of its room: shared, as a copy in that room", async () => {
        const first = answer('First.', DANA)
        const note = message('note', BOB, 'A note in the room.')
        const earlier = answer('Earlier.', DANA, { sessionId: THREAD })
        const mine = answer('Mine.', DANA, { sessionId: THREAD, parents: [given(earlier), given(note)] })
        const { result, byClosures } = await widenEachWay([note, earlier, mine], mine, privateThread)
        expect(result).to.deep.equal({ ok: true })
        expect(byClosures, 'decided by its closures').to.equal(true)
        const inThread = answer('Thread.', DANA, { sessionId: AGENT_THREAD, direct: { agents: [AGENT] }, parents: [given(first), given(note)] })
        expect((await widenEachWay([first, note, inThread], inThread, agentThread, 'everyone', everyoneUsesIt)).result, 'an agent thread’s').to.deep.equal({ ok: true })
      })

      it('it asks nothing of the share: no earlier share is counted on for it, and no agent kept', async () => {
        const note = message('note', CAROL, 'A note.')
        const carols = share(answer('R2 says.', CAROL, { direct: { records: [R2] } }))
        const bobs = answer('Built.', BOB, { parents: [given(note), given(carols)] })
        expect((await widenEachWay([note, carols, bobs], bobs)).result).to.deep.equal({ ok: true, on: [refOf(carols)] })
        const { rows, replied } = replyToANote()
        const skilled = answer('With a skill.', DANA, {
          sessionId: AGENT_THREAD,
          direct: { agents: [{ ...AGENT, skill: true }] },
          parents: [given(replied)],
        })
        expect((await widenEachWay([...rows, skilled], skilled, agentThread, 'everyone', everyoneUsesIt)).result).to.deep.equal({
          ok: true,
          limitedTo: ['X'],
        })
      })

      it('once shared, each member reads it, and an answer built on it counts on that share', async () => {
        const { rows, replied } = replyToANote()
        share(replied)
        const w = world(rows)
        for (const way of WAYS) {
          expect(await verdictOf(way, w, DANA, room, replied), `${way}: Dana`).to.equal('visible')
          for (const who of [CAROL, BOB]) expect(await verdictOf(way, w, who, room, replied), `${way}: ${who}`).to.equal('consented')
        }
        const bobs = answer('So it did.', BOB, { parents: [given(replied)] })
        expect((await widenEachWay([...rows, bobs], bobs)).result).to.deep.equal({ ok: true, on: [refOf(replied)] })
      })
    })

    describe('any other stops the share, and the reason is what the check cannot tell', () => {
      it('a note taken out of the room', async () => {
        const first = answer('First.', DANA)
        const note = message('note', DANA, 'A note.', { removedAt: T1 })
        const replied = answer('Replied.', DANA, { parents: [given(first), given(note)] })
        const rows = [first, note, replied]
        const { result, byClosures } = await widenEachWay(rows, replied)
        expect(result).to.deep.equal(NOT_WIDENABLE)
        expect(byClosures, 'decided by its closures').to.equal(true)
        expect(await why(rows, replied)).to.deep.equal(NOT_CHECKED)
      })

      it("a thread's message, which only the thread's readers read, under a share that reaches the room", async () => {
        const first = answer('First.', DANA)
        const agentNote = message('note', DANA, 'A note in the agent thread.', { sessionId: AGENT_THREAD })
        const inThread = answer('Thread.', DANA, { sessionId: AGENT_THREAD, direct: { agents: [AGENT] }, parents: [given(first), given(agentNote)] })
        const agentRows = [first, agentNote, inThread]
        expect((await widenEachWay(agentRows, inThread, agentThread, 'everyone', everyoneUsesIt)).result, 'agent thread').to.deep.equal(NOT_WIDENABLE)
        expect(await why(agentRows, inThread, agentThread, everyoneUsesIt), 'agent thread').to.deep.equal(NOT_CHECKED)
        // With people too: a share in the thread puts its answer before the room's people it names.
        expect(
          (await widenEachWay(agentRows, inThread, agentThread, { userIds: [CAROL] }, everyoneUsesIt)).result,
          'agent thread, with people',
        ).to.deep.equal(NOT_WIDENABLE)
        const earlier = answer('Earlier.', DANA, { sessionId: THREAD })
        const question = message('user_query', DANA, 'A question of my own thread.', { sessionId: THREAD })
        const mine = answer('Mine.', DANA, { sessionId: THREAD, parents: [given(earlier), given(question)] })
        const privateRows = [earlier, question, mine]
        const inPrivate = await widenEachWay(privateRows, mine, privateThread)
        expect(inPrivate.result, 'private thread').to.deep.equal(NOT_WIDENABLE)
        expect(inPrivate.byClosures, 'private thread: decided by its closures').to.equal(true)
        expect(await why(privateRows, mine, privateThread), 'private thread').to.deep.equal(NOT_CHECKED)
        // A message of another chat altogether: one a fork was made from.
        const elsewhere = message('note', DANA, 'A note of another chat.', { sessionId: FORK })
        const onIt = answer('Built.', DANA, { parents: [given(first), given(elsewhere)] })
        expect((await widenEachWay([first, elsewhere, onIt], onIt)).result, 'another chat').to.deep.equal(NOT_WIDENABLE)
      })

      it('a question that answers a card: bound to it, or in the form the app answers one with', async () => {
        const answersOfCards: Array<[string, Row]> = [
          ['bound to its card', message('user_query', BOB, 'Board, please.', { answersCard: nextId() })],
          ["a question card's", message('user_query', BOB, 'User selections:\n1. "Which deck?" → Board')],
          ["a question card's, after what the AI backend reads as space", message('user_query', BOB, '\x1c User selections:\n1. "Which deck?" → Board')],
          ["an approval card's", message('user_query', DANA, 'Tool approval: allow')],
        ]
        for (const [label, card] of answersOfCards) {
          const first = answer('First.', DANA)
          const replied = answer('Replied.', DANA, { parents: [given(first), given(card)] })
          const rows = [first, card, replied]
          expect((await widenEachWay(rows, replied)).result, label).to.deep.equal(NOT_WIDENABLE)
          expect(await why(rows, replied), label).to.deep.equal(NOT_CHECKED)
        }
      })

      it('one that opens with a quoted excerpt of an answer, which only its author reads', async () => {
        const quoting = 'Quoted excerpt:\n"""\nRevenue grew 12%.\n"""\n\nWhy?'
        for (const messageType of ['note', 'user_query'] as const) {
          const first = answer('First.', DANA)
          const quoted = message(messageType, BOB, quoting)
          const replied = answer('Replied.', DANA, { parents: [given(first), given(quoted)] })
          const rows = [first, quoted, replied]
          expect((await widenEachWay(rows, replied)).result, messageType).to.deep.equal(NOT_WIDENABLE)
          expect(await why(rows, replied), messageType).to.deep.equal(NOT_CHECKED)
        }
      })

      it('one given in full, or at a version a message never has', async () => {
        const first = answer('First.', DANA)
        const note = message('note', DANA, 'A note.')
        for (const [label, parent] of [
          ['in full', { ...given(note), mode: 'full' }],
          ['at a version', { ...given(note), ver: nextId() }],
        ] as const) {
          const replied = answer('Replied.', DANA, { parents: [given(first), parent] })
          expect((await widenEachWay([first, note, replied], replied)).result, label).to.deep.equal(NOT_WIDENABLE)
        }
      })

      it('outside the chat (a file, an egress card), any of them, as ever', async () => {
        const { rows, replied } = replyToANote()
        expect((await widenEachWay(rows, replied, room, 'outside')).result).to.deep.equal(NOT_WIDENABLE)
        expect((await widenEachWay(rows, replied, undefined, 'outside')).result, 'no chat given').to.deep.equal(NOT_WIDENABLE)
        const { rows: two, onThat } = behindAnAnswer()
        expect((await widenEachWay(two, onThat, room, 'outside')).result, 'behind an answer').to.deep.equal(NOT_WIDENABLE)
      })
    })

    it('only the note was in the way: the same answers built on no note are shared', async () => {
      const { first, second } = replyToANote()
      const replied = answer('Replied.', DANA, { parents: [given(first), given(second)] })
      const a1 = answer('Thread one.', DANA, { sessionId: AGENT_THREAD, direct: { agents: [AGENT] }, parents: [given(first), given(second), given(replied)] })
      const rows = [first, second, replied, a1]
      for (const [label, row, session] of [['in the room', second, room], ['in the room', replied, room], ['in the thread', a1, agentThread]] as const) {
        resetVisibility()
        const widened = await widenOK(row, String(row.labelVer), 'everyone', { session, deps: world(rows, everyoneUsesIt).deps })
        expect(widened.ok, label).to.equal(true)
      }
    })

    it('a real reason beside the note still refuses, and is told', async () => {
      const note = message('note', DANA, 'A note.')
      const old = answer('Before labels.', BOB)
      delete old.provenance
      const onOld = answer('Built.', DANA, { parents: [given(note), given(old)] })
      expect(await why([note, old, onOld], onOld), 'before labels').to.deep.equal({ ok: false, code: 'SHARE_UNLABELLED' })
      // An answer the previous release wrote: no label, no version and no asker on it.
      const previous = answer('From the previous release.', BOB)
      for (const field of ['provenance', 'labelVer', 'requestedBy']) delete previous[field]
      const first = answer('First.', DANA)
      const onPrevious = answer('Built.', DANA, { parents: [given(first), given(note), given(previous)] })
      const previousRows = [first, note, previous, onPrevious]
      expect((await widenEachWay(previousRows, onPrevious)).result, 'the previous release').to.deep.equal(NOT_WIDENABLE)
      expect(await why(previousRows, onPrevious), 'the previous release').to.deep.equal({ ok: false, code: 'SHARE_UNLABELLED' })
      // Carol's own answer from R2, which only Dana can open.
      const lost = answer('R2 says.', CAROL, { direct: { records: [R2] } })
      const onLost = answer('Built.', CAROL, { parents: [given(note), given(lost)] })
      expect((await widenEachWay([note, lost, onLost], onLost)).result, 'cannot open').to.deep.equal(NOT_WIDENABLE)
      expect(await why([note, lost, onLost], onLost), 'cannot open').to.deep.equal({ ok: false, code: 'SHARE_NOT_WIDENABLE' })
      // Dana's answer from R2, shared with Bob alone.
      const withBob = share(answer('R2 says.', DANA, { direct: { records: [R2] } }), { scope: 'people' })
      withBob.consentTo = [{ userId: BOB, memberSince: 'direct:2026-10-01T10:00:00.000Z' }]
      const bobs = answer('Two poems.', BOB, { parents: [given(note), given(withBob)] })
      expect((await widenEachWay([note, withBob, bobs], bobs)).result, 'shared with Bob alone').to.deep.equal(NOT_WIDENABLE)
      expect(await why([note, withBob, bobs], bobs), 'shared with Bob alone').to.deep.equal({ ok: false, code: 'SHARE_BUILT_ON_SHARED', by: DANA })
    })
  })

  it('stopped before its label came: stopped before it could be checked, not made before the checks', async () => {
    // What is saved when the stop comes first: the text so far, at a version, and no label.
    const stopped = answer('Two paragraphs so far.', BOB, { status: 'stopped' })
    delete stopped.provenance
    expect(await why([stopped], stopped)).to.deep.equal({ ok: false, code: 'SHARE_STOPPED_UNCHECKED' })
  })

  it('a stopped answer the previous release wrote, with no asker on it, is still made before the checks', async () => {
    const before = answer('From the previous release.', BOB, { status: 'stopped' })
    delete before.provenance
    delete before.requestedBy
    expect(await why([before], before)).to.deep.equal({ ok: false, code: 'SHARE_UNLABELLED' })
  })

  it('a stopped answer from before labels, with no version, is made before the checks', async () => {
    const before = answer('Before labels, stopped.', BOB, { status: 'stopped' })
    delete before.provenance
    delete before.labelVer
    resetVisibility()
    const w = world([before])
    expect(await widenOK(before, 'legacy', 'everyone', { session: room, deps: w.deps })).to.deep.equal({ ok: false, code: 'SHARE_NOT_WIDENABLE' })
    resetVisibility()
    expect(await whyNotWidenable(before, 'legacy', { session: room, deps: w.deps })).to.deep.equal({ ok: false, code: 'SHARE_UNLABELLED' })
  })

  it('a stopped answer whose label came is judged by it, as any other', async () => {
    const stopped = answer('R2 says.', CAROL, { status: 'stopped', direct: { records: [R2] } })
    expect(await why([stopped], stopped)).to.deep.equal({ ok: false, code: 'SHARE_NOT_WIDENABLE' })
  })

  it('a label that could not be completed: not checked', async () => {
    const part = answer('Partly labelled.', BOB, { complete: false })
    expect(await why([part], part)).to.deep.equal({ ok: false, code: 'SHARE_NOT_CHECKED' })
  })

  it('something the asker cannot open now: that', async () => {
    const lost = answer('R2 says.', CAROL, { direct: { records: [R2] } })
    expect(await why([lost], lost)).to.deep.equal({ ok: false, code: 'SHARE_NOT_WIDENABLE' })
  })

  it("the asker's own tools or skills stop no share of theirs, so they are never the reason", async () => {
    const own = answer('Hello Dana!', DANA, { direct: { personal: [DANA] } })
    resetVisibility()
    expect(await widenOK(own, String(own.labelVer), 'everyone', { session: room, deps: world([own]).deps })).to.deep.equal({ ok: true })
  })
})
