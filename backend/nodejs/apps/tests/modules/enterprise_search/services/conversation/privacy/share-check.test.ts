import { expect } from 'chai'
import sinon from 'sinon'
import { Logger } from '../../../../../../src/libs/services/logger.service'
import {
  ON_BEHALF_OF_READER,
  configureVisibility,
  rememberAnswerClosures,
  resetVisibility,
  visibleRows,
  widenOK,
  type ReaderSession,
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
  agentThread,
  answer,
  copyOf,
  given,
  nextId,
  privateThread,
  refOf,
  regenerate,
  room,
  share,
  stop,
  takeOut,
  unreadShares,
  world,
  ZED,
  type Row,
  type World,
} from './share-world'

/**
 * What an asker may share (`widenOK`): everything the answer used, at every depth, opened by their own access now. An
 * agent stops no share while its record says it is none of a service account and the sharer can use it; what only a
 * share gave them is named (`on`), and so are the agents the share must stay with (`limitedTo`).
 */

const NOT_WIDENABLE = { ok: false, code: 'SHARE_NOT_WIDENABLE' }
const NOT_AGENT = { ok: false, code: 'SHARE_NOT_ALLOWED_AGENT' }
const NOT_SIGN_IN = { ok: false, code: 'SHARE_NOT_ALLOWED_SIGN_IN' }

/**
 * The check with closures made by it, with closures kept since the writes, and with none (the share marks can't be read,
 * so it walks every version): all three must say the same.
 */
const widen = async (w: World, row: Row, session: ReaderSession = room, audience: WidenAudience = 'everyone'): Promise<WidenResult> => {
  resetVisibility()
  const made = await widenOK(row, String(row.labelVer), audience, { session, deps: w.deps })
  const warn = sinon.stub(Logger.prototype, 'warn')
  try {
    const walked = await widenOK(row, String(row.labelVer), audience, { session, deps: { ...w.deps, closures: unreadShares().closures } })
    expect(walked, 'the walk says the same').to.deep.equal(made)
  } finally {
    warn.restore()
  }
  resetVisibility()
  const closures = memoryClosureStore(w.store) as unknown as VisibilityDeps['closures']
  configureVisibility({ ...w.deps, closures })
  await rememberAnswerClosures(ORG, [...w.store.values()])
  resetVisibility()
  const kept = await widenOK(row, String(row.labelVer), audience, { session, deps: { ...w.deps, closures } })
  expect(kept, 'kept closures say the same').to.deep.equal(made)
  return made
}

const X = (more: Record<string, unknown> = {}): Record<string, unknown> => ({ key: 'X', kind: 'config', ...more })

describe('the share check', () => {
  afterEach(() => {
    resetVisibility()
  })

  describe('an agent does not stop a share', () => {
    it("a normal agent's answer is shared by its asker, who can use it and open what it read", async () => {
      const a = answer('X says.', CAROL, { direct: { records: [R], agents: [X()] } })
      expect(await widen(world([a], { agents: { X: [CAROL] } }), a)).to.deep.equal({ ok: true })
    })

    it('by its record now: one that runs on a service account refuses, whatever the label says; one that cannot be read refuses', async () => {
      const a = answer('X says.', CAROL, { direct: { agents: [X()] } })
      expect(await widen(world([a], { agents: { X: [CAROL] }, service: { X: true } }), a)).to.deep.equal(NOT_AGENT)
      expect(await widen(world([a], { agents: { X: [CAROL] }, service: { X: 'unknown' } }), a)).to.deep.equal(NOT_WIDENABLE)
      const failing = world([a], { agents: { X: [CAROL] } })
      failing.deps.isServiceAgent = async () => {
        throw new Error('no answer')
      }
      expect(await widen(failing, a)).to.deep.equal(NOT_WIDENABLE)
    })

    it('usable by the sharer now: not, or not to be said, refuses', async () => {
      const a = answer('X says.', CAROL, { direct: { agents: [X()] } })
      expect(await widen(world([a], { agents: { X: [BOB] } }), a)).to.deep.equal(NOT_WIDENABLE)
      expect(await widen(world([a]), a)).to.deep.equal(NOT_WIDENABLE)
    })

    it("a service agent's mark, or a search as a service identity, is never shared, at any depth, below shares and copies too", async () => {
      for (const direct of [{ agents: [X({ kind: 'service' })] }, { saVrids: ['vr-1'] }, { agents: [X({ kind: 'service' })], saVrids: ['shared-sign-in:srv'] }]) {
        const used = answer('X says.', DANA, { direct })
        const shown = share(answer('Built.', DANA, { parents: [given(used)] }))
        const built = answer('Built again.', DANA, { parents: [given(shown)] })
        const inThread = { ...answer('Privately.', DANA, { direct }), sessionId: THREAD }
        const copy = copyOf(inThread)
        const onCopy = answer('On the copy.', DANA, { parents: [given(copy)] })
        const w = world([used, shown, built, inThread, copy, onCopy], { agents: { X: [DANA] } })
        for (const row of [used, shown, built, onCopy]) expect(await widen(w, row), String(row.content)).to.deep.equal(NOT_AGENT)
      }
    })

    it("the agent that answered is asked by its record even where the label names none, on the answer and on every earlier one", async () => {
      const guest = answer('A guest agent answered.', CAROL, { respondingAgentKey: 'G' })
      const built = answer('Built.', CAROL, { parents: [given(guest)] })
      const shown = share(answer('Shown.', DANA, { parents: [given(built)] }))
      const last = answer('Last.', CAROL, { parents: [given(shown)] })
      const rows = [guest, built, shown, last]
      const agents = { G: [CAROL, DANA] }
      for (const row of rows) {
        expect(await widen(world(rows, { agents, service: { G: true } }), row), String(row.content)).to.deep.equal(NOT_AGENT)
        expect(await widen(world(rows, { agents, service: { G: 'unknown' } }), row), String(row.content)).to.deep.equal(NOT_WIDENABLE)
        expect((await widen(world(rows, { agents }), row)).ok, String(row.content)).to.equal(true)
      }
      // Usable by the sharer too, unless a share stands between.
      for (const row of [guest, built]) expect(await widen(world(rows, { agents: { G: [DANA] } }), row)).to.deep.equal(NOT_WIDENABLE)
      expect(await widen(world(rows, { agents: { G: [DANA] } }), last)).to.deep.equal({ ok: true, on: [refOf(shown)] })
    })

    it("an agent that answered an earlier version whose label has left the archive is asked too", async () => {
      const guest = answer('A guest agent answered.', CAROL, { respondingAgentKey: 'G' })
      const built = answer('Built.', CAROL, { parents: [{ id: String(guest._id), ver: 'gone', mode: 'text' }] })
      expect(await widen(world([guest, built], { agents: { G: [CAROL] }, service: { G: true } }), built)).to.deep.equal(NOT_AGENT)
      expect(await widen(world([guest, built], { agents: { G: [CAROL] } }), built)).to.deep.equal(NOT_WIDENABLE)
    })

    it('the agent whose chat or thread it is is asked, though nothing of the answer names it', async () => {
      const a = answer('Plain.', CAROL, { sessionId: AGENT_THREAD })
      expect(await widen(world([a], { agents: { X: [CAROL] } }), a, agentThread)).to.deep.equal({ ok: true })
      expect(await widen(world([a], { agents: { X: [CAROL] }, service: { X: true } }), a, agentThread)).to.deep.equal(NOT_AGENT)
      expect(await widen(world([a], { agents: { X: [CAROL] }, service: { X: 'unknown' } }), a, agentThread)).to.deep.equal(NOT_WIDENABLE)
      expect(await widen(world([a], { agents: { X: [] } }), a, agentThread)).to.deep.equal(NOT_WIDENABLE)
      expect(await widen(world([a]), a, agentThread)).to.deep.equal(NOT_WIDENABLE)
      // An agent's own chat, shared: the chat's agent.
      const inAgentChat = answer('Plain.', DANA)
      expect(await widen(world([inAgentChat], { agents: { A: [DANA] }, service: { A: true } }), inAgentChat, { ...room, agentKey: 'A' })).to.deep.equal(NOT_AGENT)
    })

    it('is asked as the sharer, on their behalf, whoever else asked or is named', async () => {
      const b = answer('Bob asked X.', BOB, { direct: { agents: [X()] }, respondingAgentKey: 'G' })
      const w = world([b], { agents: { X: [BOB], G: [BOB] } })
      expect((await widen(w, b, room, { userIds: [PRIYA] })).ok).to.equal(true)
      expect(w.serviceAsked.length).to.be.greaterThan(0)
      expect(w.serviceAsked.every((q) => q.userId === BOB && q.orgId === ORG)).to.equal(true)
      const asked = w.agentsAsked.filter((q) => q.userId === BOB)
      expect(new Set(asked.map((q) => q.key))).to.deep.equal(new Set(['X', 'G']))
      expect(asked.every((q) => q.onBehalf), 'never by a request of anyone').to.equal(true)
      expect(typeof ON_BEHALF_OF_READER).to.equal('symbol')
    })
  })

  describe("an agent's answer whose label does not say whether it used text of a skill its asker can't open: the agent's record says it now", () => {
    const EVERYONE = [DANA, CAROL, BOB]
    const agents = { X: EVERYONE, Y: EVERYONE, G: EVERYONE }
    /** Older answers of agents, and newer ones built on them, all Carol's; every way the agent is named. */
    const older = (tagset = 1) => {
      const own = answer('X said.', CAROL, { direct: { records: [R], agents: [X()] }, tagset })
      const guest = answer('A guest agent answered.', CAROL, { respondingAgentKey: 'G', tagset })
      const built = answer('Built.', CAROL, { direct: { agents: [X()] }, parents: [given(own)] })
      const further = answer('Further.', CAROL, { parents: [given(built), given(guest)] })
      return { own, guest, built, further, rows: [own, guest, built, further] }
    }
    /** In X's thread every answer is X's, whatever its label names. */
    const inThread = (tagset = 1, more: Record<string, unknown> = {}): Row => answer('In the thread.', CAROL, { sessionId: AGENT_THREAD, tagset, ...more })
    /** An answer from before labels. */
    const unlabelled = (by: string, more: Record<string, unknown> = {}): Row => {
      const row: Row = { ...answer('Before labels.', by), ...more }
      delete row.labelVer
      delete row.provenance
      return row
    }
    const legacyRef = (row: Row): { row: string; ver: string } => ({ row: String(row._id), ver: 'legacy' })

    it('no skill the sharer cannot open: it counts as one without, at every depth and however the agent is named', async () => {
      for (const tagset of [1, 0]) {
        const { rows } = older(tagset)
        for (const row of rows) expect(await widen(world(rows, { agents }), row), `${String(row.content)} ${String(tagset)}`).to.deep.equal({ ok: true })
        for (const closedSkills of [{}, { X: [], G: [] }, { X: [BOB], G: [DANA] }]) {
          const last = rows[rows.length - 1] as Row
          expect(await widen(world(rows, { agents, closedSkills }), last), JSON.stringify(closedSkills)).to.deep.equal({ ok: true })
        }
        const a = inThread(tagset)
        expect(await widen(world([a], { agents }), a, agentThread)).to.deep.equal({ ok: true })
      }
    })

    it('the reported thread: a first answer that says nothing is a parent of every later one, and none of them is refused for it', async () => {
      const hello = inThread(1, { direct: { agents: [X()] } })
      const thread = [hello]
      for (let i = 0; i < 4; i += 1)
        thread.push(answer(`Answer ${String(i)}.`, CAROL, { sessionId: AGENT_THREAD, direct: { records: [R], agents: [X()] }, parents: thread.map((row) => given(row)) }))
      const w = world(thread, { agents })
      for (const row of thread) expect(await widen(w, row, agentThread), String(row.content)).to.deep.equal({ ok: true })
    })

    it('such a skill: the share stays with the people who can use that agent, exactly as for a label that says so', async () => {
      const { own, guest, built, further, rows } = older()
      const w = world(rows, { agents, closedSkills: { X: [CAROL], G: [CAROL] } })
      expect(await widen(w, own)).to.deep.equal({ ok: true, limitedTo: ['X'] })
      expect(await widen(w, guest)).to.deep.equal({ ok: true, limitedTo: ['G'] })
      expect(await widen(w, built)).to.deep.equal({ ok: true, limitedTo: ['X'] })
      expect(await widen(w, further)).to.deep.equal({ ok: true, limitedTo: ['G', 'X'] })
      expect(await widen(world(rows, { agents, closedSkills: { G: [CAROL] } }), further)).to.deep.equal({ ok: true, limitedTo: ['G'] })
      const a = inThread()
      expect(await widen(world([a], { agents, closedSkills: { X: [CAROL] } }), a, agentThread)).to.deep.equal({ ok: true, limitedTo: ['X'] })
      const says = answer('X said.', CAROL, { direct: { records: [R], agents: [X({ skill: true })] } })
      expect(await widen(world([says], { agents }), says), 'a label that says so').to.deep.equal(await widen(w, own))
    })

    it('skills that cannot be read: as strict as that while the agent is known to be no service agent, and refused when that cannot be read either', async () => {
      const { own, built, further, rows } = older()
      const unread = { X: 'unknown', G: 'unknown' } as const
      for (const row of [own, built]) expect(await widen(world(rows, { agents, closedSkills: unread }), row)).to.deep.equal({ ok: true, limitedTo: ['X'] })
      expect(await widen(world(rows, { agents, closedSkills: unread }), further)).to.deep.equal({ ok: true, limitedTo: ['G', 'X'] })
      const failing = world(rows, { agents })
      failing.deps.hasClosedSkill = async () => {
        throw new Error('no answer')
      }
      expect(await widen(failing, own)).to.deep.equal({ ok: true, limitedTo: ['X'] })
      const unwired = world(rows, { agents })
      delete unwired.deps.hasClosedSkill
      expect(await widen(unwired, own), 'nothing asks the record').to.deep.equal({ ok: true, limitedTo: ['X'] })
      for (const closedSkills of [unread, {}, { X: [CAROL] }])
        expect(await widen(world(rows, { agents, closedSkills, service: { X: 'unknown' } }), own), JSON.stringify(closedSkills)).to.deep.equal(NOT_WIDENABLE)
    })

    it('is asked of the sharer: another person\'s older answer, shared here, is judged by what the one who builds on it can open', async () => {
      const carols = share(answer('X said.', CAROL, { direct: { personal: [CAROL], agents: [X()] }, tagset: 1 }))
      const bobs = answer('Bob.', BOB, { parents: [given(carols)] })
      const rows = [carols, bobs]
      const w = world(rows, { agents, closedSkills: { X: [BOB] } })
      expect(await widen(w, carols)).to.deep.equal({ ok: true })
      expect(await widen(w, bobs), 'below a share').to.deep.equal({ ok: true, limitedTo: ['X'], on: [refOf(carols)] })
      expect(await widen(world(rows, { agents, closedSkills: { X: [CAROL] } }), bobs)).to.deep.equal({ ok: true, on: [refOf(carols)] })
      w.skillsAsked.length = 0
      await widen(w, bobs, room, { userIds: [PRIYA] })
      expect(w.skillsAsked.length).to.be.greaterThan(0)
      expect(w.skillsAsked.every((q) => q.userId === BOB && q.orgId === ORG && q.onBehalf && q.key === 'X'), JSON.stringify(w.skillsAsked)).to.equal(true)
    })

    it('every agent it names is asked: its label\'s, the one that answered, and the one whose thread it is in', async () => {
      const a = inThread(1, { direct: { agents: [{ key: 'Y', kind: 'config' }] }, respondingAgentKey: 'G' })
      expect(await widen(world([a], { agents }), a, agentThread)).to.deep.equal({ ok: true })
      for (const key of ['X', 'Y', 'G']) {
        expect(await widen(world([a], { agents, closedSkills: { [key]: [CAROL] } }), a, agentThread), key).to.deep.equal({ ok: true, limitedTo: [key] })
        expect(await widen(world([a], { agents, closedSkills: { [key]: 'unknown' } }), a, agentThread), key).to.deep.equal({ ok: true, limitedTo: [key] })
      }
    })

    it('one by an agent nobody can name stays refused', async () => {
      const nameless: ReaderSession = { ...agentThread, agentKey: '' }
      for (const tagset of [1, 0]) {
        const a = inThread(tagset)
        const built = answer('Built.', CAROL, { sessionId: AGENT_THREAD, parents: [given(a)] })
        for (const row of [a, built]) expect(await widen(world([a, built], { agents }), row, nameless), String(row.content)).to.deep.equal(NOT_WIDENABLE)
      }
      // Nor does it help that its label names another: the thread's own can't be asked about.
      const named = inThread(1, { direct: { agents: [X()] } })
      expect(await widen(world([named], { agents }), named, nameless)).to.deep.equal(NOT_WIDENABLE)
      // An empty key says nothing of a chat that is no agent's thread, also where the labels are walked.
      const plain = share(answer('No agent.', DANA, { direct: { records: [R2] }, tagset: 1 }))
      const onPlain = answer('Built.', CAROL, { parents: [given(plain)], tagset: 1 })
      const w = world([plain, onPlain], { agents })
      expect(await widen(w, plain, { ...room, agentKey: '' })).to.deep.equal({ ok: true })
      expect(await widen(w, onPlain, { ...room, agentKey: '' })).to.deep.equal({ ok: true, on: [refOf(plain)] })
    })

    it('a service agent\'s, and a sign-in an administrator shares, are still never shared, and nobody asks about skills', async () => {
      const { rows, own, further } = older()
      const w = world(rows, { agents, service: { X: true } })
      for (const row of [own, further]) expect(await widen(w, row), String(row.content)).to.deep.equal(NOT_AGENT)
      for (const [direct, refused] of [
        [{ agents: [X({ kind: 'service' })] }, NOT_AGENT],
        [{ agents: [X()], saVrids: ['vr-1'] }, NOT_AGENT],
        [{ agents: [X()], saVrids: ['shared-sign-in:srv'] }, NOT_SIGN_IN],
      ] as const) {
        const a = answer('X said.', CAROL, { direct: { ...direct } as Record<string, unknown[]>, tagset: 1 })
        const built = answer('Built.', CAROL, { direct: { agents: [X()] }, parents: [given(a)] })
        const each = world([a, built], { agents })
        for (const row of [a, built]) expect(await widen(each, row), JSON.stringify(direct)).to.deep.equal(refused)
        expect(each.skillsAsked).to.deep.equal([])
      }
      expect(w.skillsAsked).to.deep.equal([])
    })

    it('nothing else about it changes: what the sharer cannot open or use, a label not to be relied on, and what it built on refuse as before, and nobody asks', async () => {
      const agentsOf = [X()]
      const cases: Array<[string, Row, Row[]?, Record<string, string[]>?]> = [
        ['a source she cannot open', answer('X said.', CAROL, { direct: { records: [R2], agents: agentsOf }, tagset: 1 })],
        ["another person's tools", answer('X said.', CAROL, { direct: { personal: [DANA], agents: agentsOf }, tagset: 1 })],
        ['an incomplete label', answer('X said.', CAROL, { direct: { agents: agentsOf }, complete: false, tagset: 1 })],
        ['an agent she cannot use', answer('X said.', CAROL, { direct: { agents: agentsOf }, tagset: 1 }), [], { X: [DANA] }],
        ['an agent that answered which she cannot use', answer('G said.', CAROL, { respondingAgentKey: 'G', tagset: 1 }), [], { G: [DANA] }],
      ]
      const withBob = share(answer('R2 says.', DANA, { direct: { records: [R2] } }), { scope: 'people' })
      cases.push(['a share with people behind it', answer('X said.', CAROL, { direct: { agents: agentsOf }, parents: [given(withBob)], tagset: 1 }), [withBob]])
      for (const [what, row, more = [], users = agents] of cases) {
        const w = world([row, ...more], { agents: users })
        expect(await widen(w, row), what).to.deep.equal(NOT_WIDENABLE)
        expect(w.skillsAsked, what).to.deep.equal([])
      }
    })

    it('with no label at all it is refused as before, its own or behind another; only a share here stands in for it, by its agent\'s record', async () => {
      for (const agentless of [true, false]) {
        const old = unlabelled(CAROL, agentless ? {} : { respondingAgentKey: 'G' })
        const built = answer('Built.', CAROL, { parents: [given(old)] })
        const w = world([old, built], { agents })
        for (const row of [old, built]) expect(await widen(w, row), `${String(row.content)} ${String(agentless)}`).to.deep.equal(NOT_WIDENABLE)
        expect(w.skillsAsked).to.deep.equal([])
      }
      const shared = share(unlabelled(CAROL, { respondingAgentKey: 'G' }), { ver: 'legacy', how: 'earlier' })
      const built = answer('Built.', BOB, { parents: [given(shared)] })
      const on = [legacyRef(shared)]
      const rows = [shared, built]
      expect(await widen(world(rows, { agents }), built)).to.deep.equal({ ok: true, on })
      expect(await widen(world(rows, { agents, closedSkills: { G: [BOB] } }), built)).to.deep.equal({ ok: true, limitedTo: ['G'], on })
      expect(await widen(world(rows, { agents, closedSkills: { G: 'unknown' } }), built)).to.deep.equal({ ok: true, limitedTo: ['G'], on })
      expect(await widen(world(rows, { agents, service: { G: true } }), built)).to.deep.equal(NOT_AGENT)
      expect(await widen(world(rows, { agents, service: { G: 'unknown' } }), built)).to.deep.equal(NOT_WIDENABLE)
    })

    it('with no label, the agent is the one whose chat or thread it sits in, the chat read or another', async () => {
      const inX = share(unlabelled(CAROL, { sessionId: AGENT_THREAD }), { ver: 'legacy' })
      const bobs = answer('Bob.', BOB, { sessionId: AGENT_THREAD, parents: [given(inX)] })
      const on = [legacyRef(inX)]
      expect(await widen(world([inX, bobs], { agents }), bobs, agentThread)).to.deep.equal({ ok: true, on })
      expect(await widen(world([inX, bobs], { agents, closedSkills: { X: [BOB] } }), bobs, agentThread)).to.deep.equal({ ok: true, limitedTo: ['X'], on })
      expect(await widen(world([inX, bobs], { agents }), bobs, { ...agentThread, agentKey: '' }), 'an agent nobody can name').to.deep.equal(NOT_WIDENABLE)

      // Behind a message in the room: the answer it was made from, in X's thread, which the room's own read does not name.
      const original = unlabelled(CAROL, { sessionId: AGENT_THREAD })
      const copy = copyOf(original, { fromAgentThread: true })
      const onCopy = answer('Bob.', BOB, { parents: [given(copy)] })
      const rows = [original, copy, onCopy]
      const counted = [refOf(copy)]
      expect(await widen(world(rows, { agents }), onCopy)).to.deep.equal({ ok: true, on: counted })
      expect(await widen(world(rows, { agents, closedSkills: { X: [BOB] } }), onCopy)).to.deep.equal({ ok: true, limitedTo: ['X'], on: counted })
      expect(await widen(world(rows, { agents, closedSkills: { X: 'unknown' } }), onCopy)).to.deep.equal({ ok: true, limitedTo: ['X'], on: counted })
      expect(await widen(world(rows, { agents, service: { X: true } }), onCopy)).to.deep.equal(NOT_AGENT)
      expect(await widen(world(rows, { agents, service: { X: 'unknown' } }), onCopy)).to.deep.equal(NOT_WIDENABLE)
      const unread = world(rows, { agents })
      unread.deps.chats = async () => {
        throw new Error('down')
      }
      expect(await widen(unread, onCopy), 'whose chat it is in cannot be read').to.deep.equal(NOT_WIDENABLE)
      const gone = world(rows, { agents })
      gone.deps.chats = async () => new Map()
      expect(await widen(gone, onCopy), 'the chat is gone').to.deep.equal(NOT_WIDENABLE)

      // A private thread is no agent's, so its answer is as it was; in an agent's own chat it is that agent's.
      const privately = unlabelled(DANA, { sessionId: THREAD })
      const shown = copyOf(privately)
      const onShown = answer('Bob.', BOB, { parents: [given(shown)] })
      const w = world([privately, shown, onShown], { agents })
      expect(await widen(w, onShown)).to.deep.equal({ ok: true, on: [refOf(shown)] })
      expect(w.skillsAsked).to.deep.equal([])
      const inAgentsChat = share(unlabelled(CAROL), { ver: 'legacy' })
      const hers = answer('Dana.', DANA, { parents: [given(inAgentsChat)] })
      const chat: ReaderSession = { ...room, agentKey: 'Y' }
      expect(await widen(world([inAgentsChat, hers], { agents, closedSkills: { Y: [DANA] } }), hers, chat)).to.deep.equal({ ok: true, limitedTo: ['Y'], on: [legacyRef(inAgentsChat)] })
      expect(await widen(world([inAgentsChat, hers], { agents }), hers, chat)).to.deep.equal({ ok: true, on: [legacyRef(inAgentsChat)] })
    })

    it('an older answer of no agent is as it was: labelled or not, and nobody asks anything', async () => {
      const shared = share(unlabelled(CAROL), { ver: 'legacy' })
      for (const tagset of [1, 0]) {
        const plain = answer('No agent.', CAROL, { direct: { records: [R] }, tagset })
        const built = answer('Built.', CAROL, { direct: { records: [R] }, parents: [given(plain)], tagset })
        const onShared = answer('Built.', CAROL, { parents: [given(shared)], tagset })
        const w = world([plain, built, shared, onShared])
        for (const row of [plain, built]) expect(await widen(w, row)).to.deep.equal({ ok: true })
        expect(await widen(w, onShared)).to.deep.equal({ ok: true, on: [legacyRef(shared)] })
        expect([w.skillsAsked, w.serviceAsked, w.chatsAsked]).to.deep.equal([[], [], []])
      }
    })

    it("a person's own words, quoted, are no agent's answer, in an agent's thread too", async () => {
      const question: Row = { _id: nextId(), sessionId: AGENT_THREAD, orgId: ORG, messageType: 'user_query', authorUserId: DANA, content: 'What did X say?', createdAt: T1 }
      const shown = share(answer('X said.', CAROL, { sessionId: AGENT_THREAD, direct: { personal: [CAROL], agents: [X()] }, parents: [{ id: String(question._id), ver: 'legacy', mode: 'text' }] }))
      const bobs = answer('Bob.', BOB, { sessionId: AGENT_THREAD, direct: { agents: [X()] }, parents: [given(shown)] })
      const w = world([question, shown, bobs], { agents, closedSkills: { X: [BOB] } })
      expect(await widen(w, bobs, agentThread)).to.deep.equal({ ok: true, on: [refOf(shown)] })
      expect(w.skillsAsked).to.deep.equal([])
    })

    it('a chat of many older answers of two agents asks once for each, and never for a label that says', async () => {
      const rows: Row[] = []
      for (let i = 0; i < 30; i += 1)
        rows.push(answer(`Old ${String(i)}.`, CAROL, { direct: { agents: [X({ key: i % 2 === 0 ? 'X' : 'Y' })] }, parents: rows.slice(-3).map((row) => given(row)), tagset: i % 3 === 0 ? 0 : 1 }))
      const last = answer('Last.', CAROL, { direct: { agents: [X({ key: 'G' })] }, parents: rows.map((row) => given(row)) })
      const w = world([...rows, last], { agents })
      const ask = () => widenOK(last, String(last.labelVer), 'everyone', { session: room, deps: w.deps })
      expect(await ask()).to.deep.equal({ ok: true })
      expect(w.skillsAsked.map((q) => q.key).sort()).to.deep.equal(['X', 'Y'])
      expect(w.serviceAsked.map((q) => q.key).sort()).to.deep.equal(['G', 'X', 'Y'])
      // One a label already says it of is not asked again.
      const says = answer('Says.', CAROL, { direct: { agents: [X({ skill: true })] }, parents: rows.map((row) => given(row)) })
      w.store.set(String(says._id), says)
      w.skillsAsked.length = 0
      expect(await widenOK(says, String(says.labelVer), 'everyone', { session: room, deps: w.deps })).to.deep.equal({ ok: true, limitedTo: ['X'] })
      expect(w.skillsAsked.map((q) => q.key)).to.deep.equal(['Y'])
    })

    it("outside the chat it goes to nobody unnamed; from the agents' owner, the record says whether it may go to anyone", async () => {
      const { rows } = older()
      const w = world(rows, { agents })
      for (const row of rows) expect(await widen(w, row, room, 'outside'), String(row.content)).to.deep.equal(NOT_AGENT)
      // Carol owns X and G: whether either has a skill she can't open now is asked of its record, as her.
      const owners = { X: [CAROL], G: [CAROL] }
      const owned = world(rows, { agents, owners })
      for (const row of rows) expect(await widen(owned, row, room, { outside: [ZED] }), String(row.content)).to.deep.equal({ ok: true })
      expect(owned.skillsAsked.length).to.be.greaterThan(0)
      expect(owned.skillsAsked.every((a) => a.userId === CAROL && a.onBehalf)).to.equal(true)
      const closed = world(rows, { agents, owners, closedSkills: { X: [CAROL], G: [CAROL] } })
      for (const row of rows) {
        expect(await widen(closed, row, room, { outside: [ZED] }), String(row.content)).to.deep.equal(NOT_AGENT)
        expect(await widen(closed, row, room, { outside: [BOB] }), `${String(row.content)}: to the agents' users`).to.deep.equal({ ok: true })
      }
    })
  })

  describe("a sign-in an administrator shares", () => {
    it('is never shared, at any depth, below shares and copies too, and says so', async () => {
      const used = answer('From the shared server.', DANA, { direct: { personal: [DANA], saVrids: ['shared-sign-in:srv', 'shared-sign-in:other'] } })
      const shown = share(answer('Shown.', DANA, { parents: [given(used)] }))
      const built = answer('Built.', BOB, { parents: [given(shown)] })
      const inThread = { ...answer('Privately.', DANA, { direct: { saVrids: ['shared-sign-in:srv'] } }), sessionId: THREAD }
      const copy = copyOf(inThread)
      const onCopy = answer('On the copy.', BOB, { parents: [given(copy)] })
      const w = world([used, shown, built, inThread, copy, onCopy])
      for (const row of [used, shown, built, onCopy]) expect(await widen(w, row), String(row.content)).to.deep.equal(NOT_SIGN_IN)
      expect(await widen(w, inThread, privateThread)).to.deep.equal(NOT_SIGN_IN)
    })

    it('beside anything searched as a service identity, or a service agent anywhere, it is an agent\'s', async () => {
      const mixed = answer('Mixed.', DANA, { direct: { saVrids: ['shared-sign-in:srv', 'vr-1'] } })
      expect(await widen(world([mixed]), mixed)).to.deep.equal(NOT_AGENT)
      const svc = answer('X says.', DANA, { direct: { agents: [X({ kind: 'service' })] } })
      const both = answer('Both.', DANA, { direct: { saVrids: ['shared-sign-in:srv'] }, parents: [given(svc)] })
      expect(await widen(world([svc, both], { agents: { X: [DANA] } }), both)).to.deep.equal(NOT_AGENT)
      const now = answer('Now a service agent.', DANA, { direct: { agents: [X()], saVrids: ['shared-sign-in:srv'] } })
      expect(await widen(world([now], { agents: { X: [DANA] }, service: { X: true } }), now)).to.deep.equal(NOT_AGENT)
      expect(await widen(world([now], { agents: { X: [DANA] } }), now)).to.deep.equal(NOT_SIGN_IN)
    })
  })

  describe('text of a skill the asker cannot open keeps the share with people who can use the agent', () => {
    it('on the answer itself', async () => {
      const a = answer('The floor price.', DANA, { direct: { agents: [X({ skill: true }), { key: 'Y', kind: 'config' }] } })
      expect(await widen(world([a], { agents: { X: [DANA], Y: [DANA] } }), a)).to.deep.equal({ ok: true, limitedTo: ['X'] })
    })

    it('at any depth, below a share and behind a copy too, each agent once and in one order', async () => {
      const z = answer('Z says.', DANA, { direct: { agents: [{ key: 'Z', kind: 'config', skill: true }] } })
      const x = share(answer('X says.', DANA, { direct: { agents: [X({ skill: true })] }, parents: [given(z)] }), { agents: ['X', 'Z'] })
      const built = answer('Built.', DANA, { parents: [given(x)] })
      const inThread = { ...answer('Privately.', DANA, { direct: { agents: [{ key: 'A', kind: 'config', skill: true }] } }), sessionId: THREAD }
      const copy = copyOf(inThread)
      const last = answer('Last.', DANA, { parents: [given(built), given(copy)] })
      const agents = { X: [DANA], Z: [DANA], A: [DANA] }
      const w = world([z, x, built, inThread, copy, last], { agents })
      expect(await widen(w, built)).to.deep.equal({ ok: true, limitedTo: ['X', 'Z'] })
      expect(await widen(w, last)).to.deep.equal({ ok: true, limitedTo: ['A', 'X', 'Z'] })
    })

    it('and so does a share it counts on that stays with agents, though no label of its own says so any more', async () => {
      const first = share(answer('First.', CAROL, { direct: { personal: [CAROL] } }), { agents: ['X'] })
      const second = share(answer('Second.', DANA, { direct: { records: [R2] }, parents: [given(first)] }), { on: [refOf(first)] })
      const third = answer('Third.', BOB, { parents: [given(second)] })
      const w = world([first, second, third], { agents: { X: [CAROL, DANA, BOB] } })
      expect(await widen(w, third)).to.deep.equal({ ok: true, limitedTo: ['X'], on: [refOf(second)] })
      // Counted on only by someone it covers: Bob must still be able to use X.
      const lost = world([first, second, third], { agents: { X: [CAROL, DANA] } })
      expect(await widen(lost, third)).to.deep.equal(NOT_WIDENABLE)
    })
  })

  describe('the earlier shares it counts on', () => {
    it('are named when the sharer cannot open those versions, and not when they can', async () => {
      const carols = share(answer('R says.', CAROL, { direct: { records: [R] } }))
      const bobs = answer('Bob.', BOB, { parents: [given(carols)] })
      const danas = answer('Dana.', DANA, { parents: [given(carols)] })
      const w = world([carols, bobs, danas])
      expect(await widen(w, bobs)).to.deep.equal({ ok: true, on: [refOf(carols)] })
      expect(await widen(w, danas)).to.deep.equal({ ok: true })
    })

    it('are found at every depth, each once, and an archived version by its own share', async () => {
      const carols = share(answer('R says.', CAROL, { direct: { records: [R] } }))
      const danas = share(answer('R2 says.', DANA, { direct: { records: [R2] } }))
      const bobs = answer('Bob.', BOB, { parents: [given(carols)] })
      const last = answer('Bob again.', BOB, { parents: [given(bobs), given(danas), given(carols)] })
      const w = world([carols, danas, bobs, last])
      const both = [refOf(carols), refOf(danas)].sort((a, b) => (a.row < b.row ? -1 : 1))
      expect(await widen(w, last)).to.deep.equal({ ok: true, on: both })
      regenerate(carols, 'R says it anew.', { records: [R] })
      expect(await widen(w, last)).to.deep.equal({ ok: true, on: both })
      expect((carols.consent as unknown) === undefined).to.equal(true)
    })

    it('count only while each stands whole for everyone here: stopped, or itself counting on one stopped, refuses', async () => {
      const first = share(answer('First.', CAROL, { direct: { personal: [CAROL] } }))
      const second = share(answer('Second.', DANA, { direct: { records: [R2] }, parents: [given(first)] }), { on: [refOf(first)] })
      const third = answer('Third.', BOB, { parents: [given(second)] })
      const w = world([first, second, third])
      expect(await widen(w, third)).to.deep.equal({ ok: true, on: [refOf(second)] })
      stop(first)
      expect(await widen(w, third)).to.deep.equal(NOT_WIDENABLE)
      // Carol opens the first by herself, so her second step is covered; it is not shared with everyone whole all the same.
      const carolsThird = answer('Third.', CAROL, { parents: [given(second)] })
      w.store.set(String(carolsThird._id), carolsThird)
      expect(await widen(w, carolsThird)).to.deep.equal(NOT_WIDENABLE)
      share(first)
      stop(second)
      expect(await widen(w, third)).to.deep.equal(NOT_WIDENABLE)
    })

    it('never a share with people, one of another chat, one that can\'t be read whole, or two that name each other', async () => {
      const withBob = share(answer('R2 says.', DANA, { direct: { records: [R2] } }), { scope: 'people' })
      withBob.consentTo = [{ userId: BOB, memberSince: 'direct:2026-10-01T10:00:00.000Z' }]
      const elsewhere = share(answer('R2 says.', DANA, { sessionId: FORK, direct: { records: [R2] } }))
      const unread = share(answer('R2 says.', DANA, { direct: { records: [R2] } }), { on: 'all' })
      const gone = share(answer('R2 says.', DANA, { direct: { records: [R2] } }), { on: [{ row: 'ffffffffffffffffffffffff', ver: 'eeeeeeeeeeeeeeeeeeeeeeee' }] })
      const one = share(answer('R2 says.', DANA, { direct: { records: [R2] } }))
      const two = share(answer('R2 says.', DANA, { direct: { records: [R2] } }), { on: [refOf(one)] })
      share(one, { on: [refOf(two)] })
      for (const [what, parent] of [['with people', withBob], ['another chat\'s', elsewhere], ['unread', unread], ['gone', gone], ['a loop', one]] as const) {
        const bobs = answer('Bob.', BOB, { parents: [given(parent)] })
        expect(await widen(world([withBob, elsewhere, unread, gone, one, two, bobs]), bobs), what).to.deep.equal(NOT_WIDENABLE)
      }
    })

    it('what a share gives is not what an answer below it may be missing: a label not to be relied on refuses at any depth', async () => {
      const bad = answer('Partly labelled.', CAROL, { complete: false })
      const shown = share(answer('Shown.', CAROL, { parents: [given(bad)] }))
      const built = answer('Built.', BOB, { parents: [given(shown)] })
      expect(await widen(world([bad, shown, built]), built)).to.deep.equal(NOT_WIDENABLE)
    })
  })

  describe('a file its run read', () => {
    const file = (runId?: string): Record<string, unknown> => ({ id: '', ver: '', mode: 'full', artifactId: 'art-1', ...(runId !== undefined && { runId }) })

    it('refuses the share, and says so as an agent\'s when a service agent wrote it', async () => {
      const wrote = answer('Wrote it.', DANA, { runId: 'run-w', direct: { agents: [X({ kind: 'service' })] } })
      const plain = answer('Wrote it.', DANA, { runId: 'run-p' })
      const read = answer('Read it.', DANA, { parents: [file('run-w')] })
      const readPlain = answer('Read it.', DANA, { parents: [file('run-p')] })
      const w = world([wrote, plain, read, readPlain], { agents: { X: [DANA] } })
      expect(await widen(w, read)).to.deep.equal(NOT_AGENT)
      expect(await widen(w, readPlain)).to.deep.equal(NOT_WIDENABLE)
    })

    it('below a share still says whose work it is: a service agent\'s refuses, a skill\'s keeps to its agent, one nobody is found to have written refuses', async () => {
      const behind = (writer: Row | undefined, run: string): { w: World; last: Row } => {
        const read = answer('Read it.', CAROL, { parents: [file(run)] })
        const shown = share(answer('Shown.', CAROL, { parents: [given(read)] }))
        const last = answer('Last.', BOB, { parents: [given(shown)] })
        return { w: world([read, shown, last, ...(writer ? [writer] : [])], { agents: { X: [BOB, CAROL] } }), last }
      }
      const by = (agents: unknown[]): Row => answer('Wrote it.', CAROL, { runId: 'run-w', direct: { agents } })
      const service = behind(by([X({ kind: 'service' })]), 'run-w')
      expect(await widen(service.w, service.last)).to.deep.equal(NOT_AGENT)
      const skill = behind(by([X({ skill: true })]), 'run-w')
      expect(await widen(skill.w, skill.last)).to.deep.include({ ok: true, limitedTo: ['X'] })
      const nobody = behind(undefined, 'run-gone')
      expect(await widen(nobody.w, nobody.last)).to.deep.equal(NOT_WIDENABLE)
    })
  })

  describe('this chat, for a share', () => {
    it("is the row's chat, and for a thread's row its room too; a room's answer counts on nothing of its threads", async () => {
      const inRoom = share(answer('R2 says.', DANA, { direct: { records: [R2] } }))
      const privately = answer('Bob, privately.', BOB, { sessionId: THREAD, parents: [given(inRoom)] })
      const inThread = answer('Bob, to X.', BOB, { sessionId: AGENT_THREAD, parents: [given(inRoom)] })
      const ofThread = share(answer('In the thread.', DANA, { sessionId: AGENT_THREAD, direct: { records: [R2] } }))
      const back = answer('In the room.', BOB, { parents: [given(ofThread)] })
      const w = world([inRoom, privately, inThread, ofThread, back], { agents: { X: [BOB, DANA] } })
      expect(await widen(w, privately, privateThread)).to.deep.equal({ ok: true, on: [refOf(inRoom)] })
      expect(await widen(w, inThread, agentThread)).to.deep.equal({ ok: true, on: [refOf(inRoom)] })
      expect(await widen(w, back)).to.deep.equal(NOT_WIDENABLE)
      const inSame = answer('Bob again.', BOB, { sessionId: AGENT_THREAD, parents: [given(ofThread)] })
      w.store.set(String(inSame._id), inSame)
      expect(await widen(w, inSame, agentThread)).to.deep.equal({ ok: true, on: [refOf(ofThread)] })
    })

    it('a session that is not the row\'s own refuses: it says nothing of the people who would read it', async () => {
      const a = answer('Plain.', DANA)
      const w = world([a])
      expect(await widen(w, a, privateThread)).to.deep.equal(NOT_WIDENABLE)
      expect(await widen(w, a, { ...room, _id: FORK })).to.deep.equal(NOT_WIDENABLE)
      expect((await widen(w, a)).ok).to.equal(true)
    })

    it('the sharer reads as its member: uploads of the chat and the thread count, and of no other', async () => {
      const upload = (chat: string): Record<string, unknown[]> => ({ chat: [{ conversationId: chat, recordId: 'up-1' }] })
      const inThread = answer('From the thread\'s file.', BOB, { sessionId: AGENT_THREAD, direct: { ...upload(AGENT_THREAD) } })
      const fromRoom = answer('From the room\'s file.', BOB, { sessionId: AGENT_THREAD, direct: { ...upload(ROOM) } })
      const inRoom = answer('From the room\'s file.', BOB, { direct: upload(ROOM) })
      const other = answer('From another chat\'s file.', BOB, { direct: upload(FORK) })
      const w = world([inThread, fromRoom, inRoom, other], { agents: { X: [BOB] }, chats: { [FORK]: [DANA] } })
      expect(await widen(w, inThread, agentThread)).to.deep.equal({ ok: true })
      expect(await widen(w, fromRoom, agentThread)).to.deep.equal({ ok: true })
      expect(await widen(w, inRoom)).to.deep.equal({ ok: true })
      expect(await widen(w, other)).to.deep.equal(NOT_WIDENABLE)
      // Outside the chat it is no member's: as before.
      expect(await widen(w, inRoom, room, 'outside')).to.deep.equal(NOT_WIDENABLE)
    })

    it('their own tools are theirs; another person\'s never, and a source they cannot open now refuses', async () => {
      const own = answer('From my tracker.', BOB, { direct: { personal: [BOB] } })
      const carols = answer('From Carol\'s tracker.', BOB, { direct: { personal: [CAROL] } })
      const r = answer('R says.', BOB, { direct: { records: [R] } })
      const w = world([own, carols, r])
      expect(await widen(w, own)).to.deep.equal({ ok: true })
      for (const row of [carols, r]) expect(await widen(w, row)).to.deep.equal(NOT_WIDENABLE)
    })
  })

  describe('a shared copy behind it', () => {
    // An agent's answer to Dana in its thread of the room, which Carol and Bob use too, or a private one of Dana's.
    const told = (direct: Record<string, unknown[]> = { records: [R] }, chat = AGENT_THREAD): { original: Row; copy: Row; w: World; bobs: Row; carols: Row } => {
      const original = answer('To Dana.', DANA, { sessionId: chat, direct })
      const copy = copyOf(original, chat === AGENT_THREAD ? { fromAgentThread: true } : {})
      const bobs = answer('Bob.', BOB, { parents: [given(copy)] })
      const carols = answer('Carol.', CAROL, { parents: [given(copy)] })
      return { original, copy, bobs, carols, w: world([original, copy, bobs, carols], { agents: { X: [DANA, CAROL, BOB] } }) }
    }

    it('in this chat counts as shared here, and is counted on unless the sharer can open the original whole', async () => {
      const { copy, bobs, carols, w } = told()
      expect(await widen(w, bobs)).to.deep.equal({ ok: true, on: [refOf(copy)] })
      expect(refOf(copy).ver).to.equal('')
      expect(await widen(w, carols)).to.deep.equal({ ok: true })
    })

    it('is counted on by everyone who does not read the thread it was made in, whatever they can open', async () => {
      for (const direct of [{ records: [R] }, {}]) {
        const { copy, bobs, carols, w } = told(direct, THREAD)
        const danas = answer('Dana.', DANA, { parents: [given(copy)] })
        w.store.set(String(danas._id), danas)
        for (const row of [bobs, carols]) expect(await widen(w, row)).to.deep.equal({ ok: true, on: [refOf(copy)] })
        expect(await widen(w, danas), 'the private answer is hers').to.deep.equal({ ok: true })
        takeOut(copy)
        for (const row of [bobs, carols]) expect(await widen(w, row)).to.deep.equal(NOT_WIDENABLE)
        expect(await widen(w, danas)).to.deep.equal({ ok: true })
      }
      const { copy, w } = told({})
      const priyas = answer('Priya.', PRIYA, { parents: [given(copy)] })
      w.store.set(String(priyas._id), priyas)
      expect(await widen(w, priyas), 'Priya does not use the agent').to.deep.equal({ ok: true, on: [refOf(copy)] })
      takeOut(copy)
      expect(await widen(w, priyas)).to.deep.equal(NOT_WIDENABLE)
    })

    it('whose thread cannot be read now is counted on, not taken as read', async () => {
      const { copy, carols, w } = told()
      w.deps.chats = async () => {
        throw new Error('down')
      }
      expect(await widen(w, carols)).to.deep.equal({ ok: true, on: [refOf(copy)] })
    })

    it('one Share to chat stops no later share: an unrelated answer given it and an earlier one built on it', async () => {
      const { copy, bobs, w } = told()
      const unrelated = answer('Twelve months.', PRIYA, { parents: [given(copy), given(bobs)] })
      w.store.set(String(unrelated._id), unrelated)
      expect(await widen(w, unrelated)).to.deep.equal({ ok: true, on: [refOf(copy)] })
    })

    it('in another chat, or taken out, it is the answer it was made from', async () => {
      const { original, copy, bobs, carols, w } = told()
      takeOut(copy)
      expect(await widen(w, bobs)).to.deep.equal(NOT_WIDENABLE)
      expect(await widen(w, carols)).to.deep.equal({ ok: true })
      const second = copyOf(original, { fromAgentThread: true })
      w.store.set(String(second._id), second)
      copy.again = second._id
      // The copy that took its place stands for it; the one given is what is counted on.
      expect(await widen(w, bobs)).to.deep.equal({ ok: true, on: [refOf(copy)] })
      const elsewhere = { ...copyOf(original, { fromAgentThread: true }), sessionId: FORK }
      const built = answer('Bob.', BOB, { parents: [given(elsewhere)] })
      const carolsBuilt = answer('Carol.', CAROL, { parents: [given(elsewhere)] })
      for (const row of [elsewhere, built, carolsBuilt]) w.store.set(String(row._id), row)
      expect(await widen(w, built)).to.deep.equal(NOT_WIDENABLE)
      expect(await widen(w, carolsBuilt)).to.deep.equal({ ok: true })
    })

    it('taken out with nothing kept of its answer, nobody can share what was built on it', async () => {
      const { copy, bobs, carols, w } = told()
      takeOut(copy, { kept: false })
      for (const row of [bobs, carols]) expect(await widen(w, row)).to.deep.equal(NOT_WIDENABLE)
    })

    it('from before copies kept their version, it counts while its answer is from before labels, and refuses otherwise', async () => {
      const { original, copy, bobs, w } = told()
      delete (copy.sharedFrom as Record<string, unknown>).ver
      expect(await widen(w, bobs)).to.deep.equal(NOT_WIDENABLE)
      delete original.labelVer
      delete original.provenance
      expect(await widen(w, bobs)).to.deep.equal({ ok: true, on: [refOf(copy)] })
      original.respondingAgentKey = 'G'
      expect(await widen(world([original, copy, bobs], { agents: { X: [BOB], G: [BOB] }, service: { G: true } }), bobs)).to.deep.equal(NOT_AGENT)
    })

    it('that counts on a share in turn counts only while that share is with everyone here', async () => {
      const first = share(answer('R2 says.', DANA, { direct: { records: [R2] } }))
      const original = answer('To Carol.', CAROL, { sessionId: AGENT_THREAD, parents: [given(first)] })
      const copy = copyOf(original, { fromAgentThread: true, on: [refOf(first)] })
      const bobs = answer('Bob.', BOB, { parents: [given(copy)] })
      const w = world([first, original, copy, bobs], { agents: { X: [CAROL, BOB] } })
      // Bob reads the thread, and passes its answer by the share it was built on: what his own share counts on.
      expect(await widen(w, bobs)).to.deep.equal({ ok: true, on: [refOf(first)] })
      stop(first)
      expect(await widen(w, bobs)).to.deep.equal(NOT_WIDENABLE)
    })

    it('that counts on a share only the sharer opens now is not whole for everyone here', async () => {
      // Carol opens what the copy counts on herself, and not the answer it was made from, whose thread she reads.
      const first = share(answer('Carol\'s own.', CAROL, { direct: { personal: [CAROL] } }))
      const original = answer('To Dana.', DANA, { sessionId: AGENT_THREAD, direct: { records: [R2] }, parents: [given(first)] })
      const copy = copyOf(original, { fromAgentThread: true, on: [refOf(first)] })
      const carols = answer('Carol.', CAROL, { parents: [given(copy)] })
      const w = world([first, original, copy, carols], { agents: { X: [DANA, CAROL] } })
      expect(await widen(w, carols)).to.deep.equal({ ok: true, on: [refOf(copy)] })
      stop(first)
      expect(await widen(w, carols)).to.deep.equal(NOT_WIDENABLE)
    })

    it('an agent of the copied answer keeps the share with its users, and stops none', async () => {
      const { w, bobs } = told({ agents: [X({ skill: true })] })
      w.deps.canUseAgent = async (who) => who.userId === DANA
      expect(await widen(w, bobs)).to.deep.include({ ok: true, limitedTo: ['X'] })
    })
  })

  describe('outside the chat, nothing is looser', () => {
    /** A grant of a file made from `row` to the people `to` names, or to nobody named. */
    const outside = (w: World, row: Row, to?: string[]): Promise<WidenResult> =>
      widen(w, row, room, to === undefined ? 'outside' : { outside: to })

    it("an agent on its asker's own access goes only to people who can use it, on the answer or an earlier one, by label or by who answered", async () => {
      const config = answer('X says.', DANA, { direct: { agents: [X()] } })
      const builtOnConfig = answer('Built.', DANA, { parents: [given(config)] })
      const guest = answer('A guest agent answered.', DANA, { respondingAgentKey: 'X' })
      const builtOnGuest = answer('Built.', DANA, { parents: [given(guest)] })
      const w = world([config, builtOnConfig, guest, builtOnGuest], { agents: { X: [DANA, ZED] } })
      for (const row of [config, builtOnConfig, guest, builtOnGuest]) {
        expect((await widen(w, row)).ok, 'in the chat').to.equal(true)
        expect(await outside(w, row, [ZED]), 'to its users').to.deep.equal({ ok: true })
        expect(await outside(w, row, [ZED, BOB]), "to one who can't use it").to.deep.equal(NOT_AGENT)
        expect(await outside(w, row), 'to nobody named').to.deep.equal(NOT_AGENT)
      }
      // Each person it goes to is asked about as themself; the agent's record, as its asker.
      expect(w.agentsAsked.filter((a) => a.userId === ZED || a.userId === BOB).every((a) => a.onBehalf)).to.equal(true)
      expect(w.serviceAsked.length).to.be.greaterThan(0)
      expect(w.serviceAsked.every((a) => a.userId === DANA)).to.equal(true)
    })

    it("from the agent's owner it goes to anyone, unless the run was given text of a skill the owner can't open", async () => {
      const config = answer('X says.', DANA, { direct: { agents: [X()] } })
      const builtOnConfig = answer('Built.', DANA, { parents: [given(config)] })
      const skilled = answer('X says.', DANA, { direct: { agents: [X({ skill: true })] } })
      const older = answer('X says.', DANA, { tagset: 1, direct: { agents: [X()] } })
      const rows = [config, builtOnConfig, skilled, older]
      const w = world(rows, { agents: { X: [DANA] }, owners: { X: [DANA] } })
      for (const row of [config, builtOnConfig, older]) {
        expect(await outside(w, row, [ZED]), 'to anyone').to.deep.equal({ ok: true })
        expect(await outside(w, row), 'to nobody named').to.deep.equal({ ok: true })
      }
      expect(await outside(w, skilled, [ZED]), 'a skill she cannot open').to.deep.equal(NOT_AGENT)
      expect(w.ownersAsked.length).to.be.greaterThan(0)
      expect(w.ownersAsked.every((a) => a.userId === DANA && a.onBehalf)).to.equal(true)
      // A label too old to say: the agent's record says whether it has such a skill now, and what it can't say counts.
      for (const closed of [[DANA], 'unknown'] as Array<string[] | 'unknown'>) {
        const now = world(rows, { agents: { X: [DANA] }, owners: { X: [DANA] }, closedSkills: { X: closed } })
        expect(await outside(now, older, [ZED]), String(closed)).to.deep.equal(NOT_AGENT)
        expect(await outside(now, config, [ZED]), `${String(closed)}: a label that says none`).to.deep.equal({ ok: true })
      }
      // Such a skill still goes to the agent's users; an owner that can't be told is nobody.
      const users = world(rows, { agents: { X: [DANA, ZED] }, owners: { X: 'unknown' } })
      expect(await outside(users, skilled, [ZED])).to.deep.equal({ ok: true })
      expect(await outside(users, config, [BOB])).to.deep.equal(NOT_AGENT)
    })

    it('its asker must be able to use every agent behind it now, and each is asked by its record', async () => {
      const config = answer('X says.', DANA, { direct: { agents: [X()] } })
      const guest = answer('A guest agent answered.', DANA, { respondingAgentKey: 'X' })
      for (const row of [config, guest]) {
        const notHers = world([row], { agents: { X: [ZED] }, owners: { X: [DANA] } })
        expect(await outside(notHers, row, [ZED]), 'she cannot use it').to.deep.equal(NOT_WIDENABLE)
        const unsaid = world([row], { owners: { X: [DANA] } })
        expect(await outside(unsaid, row, [ZED]), 'that cannot be said').to.deep.equal(NOT_WIDENABLE)
        const service = world([row], { agents: { X: [DANA, ZED] }, owners: { X: [DANA] }, service: { X: true } })
        expect(await outside(service, row, [ZED]), 'a service agent by its record').to.deep.equal(NOT_AGENT)
        const unread = world([row], { agents: { X: [DANA, ZED] }, owners: { X: [DANA] }, service: { X: 'unknown' } })
        expect(await outside(unread, row, [ZED]), 'a record that cannot be read').to.deep.equal(NOT_WIDENABLE)
      }
    })

    it("a service agent's work never goes, by its label, at any depth, to the agent's users or from its owner either", async () => {
      const service = answer('S says.', DANA, { direct: { agents: [X({ kind: 'service' })] } })
      const built = answer('Built.', DANA, { parents: [given(service)] })
      const w = world([service, built], { agents: { X: [DANA, ZED] }, owners: { X: [DANA] } })
      for (const row of [service, built]) expect(await outside(w, row, [ZED])).to.deep.equal(NOT_AGENT)
    })

    it("an answer of an agent's chat or thread goes only to people who can use its agent", async () => {
      const a = answer('Plain.', DANA, { sessionId: AGENT_THREAD })
      const w = world([a], { agents: { X: [DANA, ZED] } })
      const grant = (to: WidenAudience, session?: ReaderSession): Promise<WidenResult> =>
        widenOK(a, String(a.labelVer), to, { ...(session && { session }), deps: w.deps })
      expect(await grant('outside', agentThread)).to.deep.equal(NOT_AGENT)
      expect(await grant({ outside: [BOB] }, agentThread)).to.deep.equal(NOT_AGENT)
      expect(await grant({ outside: [ZED] }, agentThread)).to.deep.equal({ ok: true })
      expect((await grant('outside')).ok, 'no session of its own given').to.equal(true)
    })

    it('a file its run read goes as the version of the answer that wrote it does', async () => {
      const file = (runId: string): Record<string, unknown> => ({ id: '', ver: '', mode: 'full', artifactId: 'art-1', runId })
      const wrote = answer('Wrote it: R2.', DANA, { runId: 'run-w', direct: { records: [R2] } })
      const edited = answer('Added a totals row.', DANA, { parents: [file('run-w')] })
      const twice = answer('And a chart.', DANA, { parents: [given(edited), file('run-w')] })
      const bobs = answer('Bob wrote it.', BOB, { runId: 'run-b', direct: { personal: [BOB] } })
      const onBobs = answer('Edited.', DANA, { parents: [file('run-b')] })
      const service = answer('S wrote it.', DANA, { runId: 'run-s', direct: { agents: [X({ kind: 'service' })] } })
      const onService = answer('Edited.', DANA, { parents: [file('run-s')] })
      const byX = answer('X wrote it.', DANA, { runId: 'run-x', direct: { agents: [X()] } })
      const onX = answer('Edited.', DANA, { parents: [file('run-x')] })
      const carols = share(answer("Carol's own.", CAROL, { direct: { personal: [CAROL] } }))
      const onShared = answer('Wrote it.', DANA, { runId: 'run-c', parents: [given(carols)] })
      const onSharedFile = answer('Edited.', DANA, { parents: [file('run-c')] })
      const nobody = answer('Edited.', DANA, { parents: [file('run-gone')] })
      // Written again since by another run, with Bob's tools: the reading run was given the version run-a wrote.
      const again = answer('Wrote it: R2.', DANA, { runId: 'run-a', direct: { records: [R2] } })
      const later = answer("Now with Bob's tools.", DANA, { direct: { personal: [BOB] } })
      again.labelHistory = [{ ver: again.labelVer, runId: 'run-a', provenance: again.provenance }]
      Object.assign(again, { runId: 'run-a2', labelVer: later.labelVer, provenance: later.provenance, content: later.content, parts: later.parts })
      const onAgain = answer('Edited.', DANA, { parents: [file('run-a')] })
      const onLater = answer('Edited.', DANA, { parents: [file('run-a2')] })
      const rows = [wrote, edited, twice, bobs, onBobs, service, onService, byX, onX, carols, onShared, onSharedFile, nobody, again, onAgain, onLater]
      const w = world(rows, { agents: { X: [DANA, CAROL] } })
      for (const row of [edited, twice, onAgain]) expect(await outside(w, row, [ZED]), 'her own').to.deep.equal({ ok: true })
      expect(await outside(w, onBobs, [ZED]), "another's tools").to.deep.equal(NOT_WIDENABLE)
      expect(await outside(w, onLater, [ZED]), "the version with another's tools").to.deep.equal(NOT_WIDENABLE)
      expect(await outside(w, onService, [ZED]), 'a service agent wrote it').to.deep.equal(NOT_AGENT)
      expect(await outside(w, onX, [CAROL]), "an agent's, to its users").to.deep.equal({ ok: true })
      expect(await outside(w, onX, [ZED]), "an agent's, to one who can't use it").to.deep.equal(NOT_AGENT)
      expect(await outside(w, onSharedFile, [ZED]), 'its writer built on a share').to.deep.equal(NOT_WIDENABLE)
      expect(await outside(w, nobody, [ZED]), 'no run is found to have written it').to.deep.equal(NOT_WIDENABLE)
      // In the chat a file still refuses (B1 is not this change).
      expect(await widen(w, edited)).to.deep.equal(NOT_WIDENABLE)
    })

    it('no shared sign-in, no share of any kind, and no copy, live or not', async () => {
      const signedIn = answer('From the shared server.', DANA, { direct: { saVrids: ['shared-sign-in:srv'] } })
      const carols = share(answer('Carol\'s own.', CAROL, { direct: { personal: [CAROL] } }))
      const onShare = answer('Built.', DANA, { parents: [given(carols)] })
      const original = answer('Dana, privately.', DANA, { sessionId: THREAD })
      const copy = copyOf(original)
      // Beside a plain answer, so that the check walks where the share marks can't be read.
      const plain = answer('Plain.', DANA)
      const onCopy = answer('Built.', DANA, { parents: [given(plain), given(copy)] })
      const w = world([signedIn, carols, onShare, original, copy, plain, onCopy])
      expect(await outside(w, signedIn)).to.deep.equal(NOT_AGENT)
      expect(await outside(w, signedIn, [CAROL])).to.deep.equal(NOT_AGENT)
      for (const row of [onShare, onCopy]) {
        expect((await widen(w, row)).ok, 'in the chat').to.equal(true)
        expect(await outside(w, row)).to.deep.equal(NOT_WIDENABLE)
        expect(await outside(w, row, [CAROL]), 'to the one who shared it').to.deep.equal(NOT_WIDENABLE)
      }
    })

    it('what its asker opens by themself still goes', async () => {
      const a = answer('R says.', CAROL, { direct: { records: [R] } })
      const built = answer('Built.', CAROL, { parents: [given(a)] })
      expect(await outside(world([a, built]), built)).to.deep.equal({ ok: true })
      expect(await outside(world([a, built]), built, [ZED])).to.deep.equal({ ok: true })
    })
  })

  it('a share that was widened as the check says covers whom it says, and ends with what it counted on', async () => {
    // The check and the read together: Bob shares on the strength of a copy, which is then taken out and shared again.
    const original = answer('Dana, privately: CANARY.', DANA, { sessionId: THREAD, direct: { records: [R2] } })
    const copy = copyOf(original)
    const bobs = answer('Bob: CANARY.', BOB, { direct: { personal: [BOB] }, parents: [given(copy)] })
    const w = world([original, copy, bobs])
    const widened = await widen(w, bobs)
    expect(widened).to.deep.equal({ ok: true, on: [refOf(copy)] })
    share(bobs, { on: widened.on })
    const read = async (): Promise<string | undefined> =>
      (await visibleRows({ userId: PRIYA, orgId: ORG }, room, [bobs], { deps: w.deps, member: true })).get(String(bobs._id))?.verdict
    expect(await read()).to.equal('consented')
    takeOut(copy)
    expect(await read()).to.equal('hidden')
    const second = copyOf(original)
    w.store.set(String(second._id), second)
    copy.again = second._id
    expect(await read()).to.equal('consented')
  })
})
