import { expect } from 'chai'
import sinon from 'sinon'
import {
  configureVisibility,
  rememberAnswerClosures,
  resetVisibility,
  visibleRows,
  widenOK,
  type Reader,
  type ReaderRow,
  type ReaderSession,
  type VisibilityDeps,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import * as walk from './reference/visibility-walk'
import { VerdictStore } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/verdict-store'
import { archivedLabelHistory, labelContentHash } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label'
import { CHAIN_MAX, CLOSURE_IDS_MAX } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/closure'
import { memoryClosureStore, type Row } from './closure-world'
import { Logger } from '../../../../../../src/libs/services/logger.service'

/**
 * Closures kept beside the rows: made when an answer is written, taken by every read after, made again when a share
 * since could change them; a version whose label left its row's archive is judged by the closure it kept.
 */

const ORG = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const ROOM = 'bbbbbbbbbbbbbbbbbbbbbbbb'
const DANA = 'd00000000000000000000001'
const CAROL = 'c00000000000000000000002'
const BOB = 'b00000000000000000000003'
const T0 = new Date('2026-10-01T10:00:00Z')
const DIRECT_KEYS = ['records', 'vrids', 'nodes', 'saVrids', 'personal', 'agents', 'chat', 'projects', 'marks']

const session: ReaderSession = {
  _id: ROOM,
  orgId: ORG,
  userId: DANA,
  sharedWith: [
    { userId: CAROL, accessLevel: 'write', addedAt: T0 },
    { userId: BOB, accessLevel: 'write', addedAt: T0 },
  ],
}

let n = 0
/** Ids made in time order, as ObjectIds are. */
const nextId = (): string => {
  n += 1
  return `${(0x6abe2000 + n).toString(16)}${n.toString(16).padStart(16, '0')}`
}

const labelOf = (ver: string, content: string, parts: unknown[], records: string[], parents: unknown[]): Record<string, unknown> => ({
  v: 5,
  ver,
  contentHash: labelContentHash(content, parts),
  direct: { ...Object.fromEntries(DIRECT_KEYS.map((k) => [k, []])), records },
  parents,
  complete: true,
  tagset: 1,
  pub: false,
})

const answer = (requestedBy: string, records: string[], parents: unknown[] = []): Row => {
  const labelVer = nextId()
  const content = `Answer ${labelVer}`
  const parts = [{ type: 'text', content }]
  return {
    _id: nextId(),
    sessionId: ROOM,
    orgId: ORG,
    messageType: 'bot_response',
    requestedBy,
    content,
    parts,
    labelVer,
    runId: `run-${labelVer}`,
    provenance: labelOf(labelVer, content, parts, records, parents),
  }
}

/** The row written again (a regenerate): its version archived, a new one in its place. */
const rewrite = (row: Row, records: string[]): void => {
  row.labelHistory = archivedLabelHistory(row) ?? []
  delete row.consent
  const labelVer = nextId()
  const content = `Rewritten ${labelVer}`
  const parts = [{ type: 'text', content }]
  row.labelVer = labelVer
  row.content = content
  row.parts = parts
  row.provenance = labelOf(labelVer, content, parts, records, [])
}

const parentOf = (row: Row, mode = 'text') => ({ id: String(row._id), ver: String(row.labelVer), mode })

interface Counts {
  labels: number
  access: number
}

const world = (opens: Record<string, (id: string) => boolean>) => {
  const store = new Map<string, Row>()
  const counts: Counts = { labels: 0, access: 0 }
  const kept = memoryClosureStore(store)
  const deps: Partial<VisibilityDeps> = {
    readable: async (who, ids) => {
      counts.access += 1
      const can = opens[who.userId] ?? (() => false)
      return { readable: new Set(ids.filter(can)), asked: new Set(ids), deleted: new Set(), failed: false, validUntil: Date.now() + 30_000 }
    },
    labels: async (_org, ids) => {
      counts.labels += 1
      return new Map(ids.flatMap((id) => (store.has(id) ? [[id, store.get(id) as ReaderRow]] : [])))
    },
    runs: async () => [],
    canUseAgent: async () => undefined,
    canReadConversation: async () => undefined,
    canReadProject: async () => undefined,
    isServiceAgent: async () => false,
    sharedText: (text) => text,
    switchAt: async () => T0.getTime(),
    labelRule: async () => true,
    store: new VerdictStore(),
    closures: kept,
  }
  configureVisibility(deps)
  const write = async (row: Row): Promise<void> => {
    store.set(String(row._id), row)
    await rememberAnswerClosures(ORG, [row])
  }
  return { store, counts, kept, deps, write }
}

const reader = (userId: string): Reader => ({ userId, orgId: ORG })
const everything = (): boolean => true

describe('closures kept beside the rows', () => {
  afterEach(() => {
    resetVisibility()
    walk.resetVisibility()
  })

  it('each answer gets its closure when written; a read of a 2,000-answer chat reads its page and the kept closures, never the chain', async () => {
    const w = world({ [DANA]: everything, [CAROL]: everything })
    const rows: Row[] = []
    for (let t = 0; t < 2_000; t += 1) {
      const row = answer(t % 2 === 0 ? DANA : CAROL, [`rec-${t % 40}`], rows.slice(-50).map((r) => parentOf(r)))
      rows.push(row)
      await w.write(row)
    }
    expect(w.kept.docs.size).to.equal(2_000)
    const page = rows.slice(-10)
    resetVisibility()
    configureVisibility(w.deps)
    w.counts.labels = 0
    w.counts.access = 0
    const gets = w.kept.counts.gets
    const verdicts = await visibleRows(reader(CAROL), session, page, { member: true })
    expect(page.map((r) => verdicts.get(String(r._id))?.verdict)).to.deep.equal(page.map(() => 'visible'))
    expect(w.counts.labels, 'the page itself').to.equal(1)
    expect(w.kept.counts.gets - gets, 'the page closures, then their chains').to.be.at.most(2)
    expect(w.counts.access).to.equal(1)
    // Each kept closure adds to the one it was built on; none is longer than its limit.
    for (const doc of w.kept.docs.values()) expect((doc.chain as unknown[]).length).to.be.at.most(CHAIN_MAX)
  })

  it('a share made after a closure was kept: a reader it covers sees the answer built on it, as the walk does', async () => {
    const w = world({ [DANA]: everything, [CAROL]: everything, [BOB]: (id) => id !== 'rec-r' })
    const carols = answer(CAROL, ['rec-r'])
    await w.write(carols)
    const bobs = answer(BOB, ['rec-s'], [parentOf(carols)])
    await w.write(bobs)
    expect((await visibleRows(reader(BOB), session, [bobs], { member: true })).get(String(bobs._id))?.verdict).to.equal('hidden')
    carols.consent = { by: CAROL, at: new Date(), ver: carols.labelVer, scope: 'everyone' }
    const after = await visibleRows(reader(BOB), session, [bobs], { member: true })
    const theirs = await walk.visibleRows(reader(BOB), session, [bobs], { member: true, deps: w.deps })
    expect(after.get(String(bobs._id))?.verdict).to.equal('visible')
    expect(theirs.get(String(bobs._id))?.verdict).to.equal('visible')
    // And a share stopped again: the alternative is read as it is now.
    delete carols.consent
    expect((await visibleRows(reader(BOB), session, [bobs], { member: true })).get(String(bobs._id))?.verdict).to.equal('hidden')
  })

  it("an answer rewritten 21 times: its first version leaves the archive, and Carol's follow-up on it is judged by the closure it kept", async () => {
    const w = world({ [DANA]: everything, [CAROL]: everything, [BOB]: (id) => id !== 'rec-first' })
    const danas = answer(DANA, ['rec-first'])
    await w.write(danas)
    const first = String(danas.labelVer)
    const carols = answer(CAROL, ['rec-c'], [parentOf(danas, 'full')])
    await w.write(carols)
    for (let i = 0; i < 21; i += 1) {
      rewrite(danas, [`rec-later-${i}`])
      await w.write(danas)
    }
    expect((danas.labelHistory as Array<{ ver: string }>).some((e) => e.ver === first)).to.equal(false)
    const forCarol = await visibleRows(reader(CAROL), session, [carols], { member: true })
    expect(forCarol.get(String(carols._id))?.verdict).to.equal('visible')
    const forBob = await visibleRows(reader(BOB), session, [carols], { member: true })
    expect(forBob.get(String(carols._id))?.verdict).to.equal('hidden')
    // The walk found the version gone and hid Carol's own follow-up from her.
    const before = await walk.visibleRows(reader(CAROL), session, [carols], { member: true, deps: w.deps })
    expect(before.get(String(carols._id))?.verdict).to.equal('hidden')
  })

  it(`a closure past ${String(CLOSURE_IDS_MAX)} ids is not kept whole: what is built on it hides, from its asker too`, async () => {
    const w = world({ [DANA]: everything })
    const many = (prefix: string, count: number) => Array.from({ length: count }, (_, i) => `${prefix}-${String(i)}`)
    const a1 = answer(DANA, many('a', 30_000))
    await w.write(a1)
    const a2 = answer(DANA, many('b', 25_000), [parentOf(a1, 'full')])
    await w.write(a2)
    const a3 = answer(DANA, ['rec-c'], [parentOf(a2, 'full')])
    await w.write(a3)
    const verdicts = await visibleRows(reader(DANA), session, [a2, a3], { member: true })
    expect(verdicts.get(String(a2._id))?.verdict).to.equal('visible')
    expect(verdicts.get(String(a3._id))).to.include({ verdict: 'hidden', reason: 'parent' })
  })

  it('50 readers at once of an answer stored without its closure: it is made once, and each gets their own verdict', async () => {
    const w = world({ [DANA]: everything, [CAROL]: everything, [BOB]: (id) => id !== 'rec-7' })
    const rows: Row[] = []
    for (let t = 0; t < 60; t += 1) {
      const row = answer(DANA, [`rec-${t % 10}`], rows.slice(-50).map((r) => parentOf(r)))
      rows.push(row)
      await w.write(row)
    }
    const fresh = answer(CAROL, ['rec-new'], rows.slice(-50).map((r) => parentOf(r)))
    w.store.set(String(fresh._id), fresh)
    const keptBefore = w.kept.counts.kept
    const people = Array.from({ length: 50 }, (_, i) => (i % 5 === 0 ? BOB : i % 2 === 0 ? DANA : CAROL))
    const verdicts = await Promise.all(people.map((who) => visibleRows(reader(who), session, [fresh], { member: true })))
    expect(w.kept.counts.kept - keptBefore).to.equal(1)
    verdicts.forEach((v, i) => expect(v.get(String(fresh._id))?.verdict, String(i)).to.equal(people[i] === BOB ? 'hidden' : 'visible'))
  })

  it('a closure store that does not answer is reported once, and once when it answers again; every read still judges right', async () => {
    const w = world({ [DANA]: everything, [CAROL]: everything, [BOB]: (id) => id !== 'rec-1' })
    const a1 = answer(DANA, ['rec-1'])
    await w.write(a1)
    const a2 = answer(DANA, ['rec-2'], [parentOf(a1)])
    await w.write(a2)
    // A new process: nothing of the kept closures in memory, so every read asks the store.
    resetVisibility()
    let down = true
    const unless = <T>(work: () => Promise<T>): Promise<T> => (down ? Promise.reject(new Error('store down')) : work())
    configureVisibility({
      ...w.deps,
      store: new VerdictStore(),
      closures: {
        get: (org, keys) => unless(() => w.kept.get(org, keys)),
        put: (org, closures) => unless(() => w.kept.put(org, closures)),
        shares: (org, ids) => unless(() => w.kept.shares(org, ids)),
      },
    })
    const warn = sinon.stub(Logger.prototype, 'warn')
    const info = sinon.stub(Logger.prototype, 'info')
    try {
      for (const who of [DANA, BOB, CAROL]) {
        const verdicts = await visibleRows(reader(who), session, [a2], { member: true })
        expect(verdicts.get(String(a2._id))?.verdict, who).to.equal(who === BOB ? 'hidden' : 'visible')
      }
      const said = (stub: sinon.SinonStub, text: string): number => stub.getCalls().filter((c) => String(c.args[0]).startsWith(text)).length
      expect(said(warn, 'The answer closure store did not answer')).to.equal(1)
      expect(said(info, 'The answer closure store answers again')).to.equal(0)
      down = false
      // A verdict not kept from before, so the read asks the store again.
      configureVisibility({ store: new VerdictStore() })
      const after = await visibleRows(reader(BOB), session, [a2], { member: true })
      expect(after.get(String(a2._id))?.verdict).to.equal('hidden')
      expect(said(info, 'The answer closure store answers again')).to.equal(1)
      expect(said(warn, 'The answer closure store did not answer')).to.equal(1)
    } finally {
      warn.restore()
      info.restore()
    }
  })

  it("an answer given one from before labels whole, kept before the owner's one-step consent to it, may be shared as the walk says", async () => {
    const IVY = 'e00000000000000000000004'
    const w = world({ [DANA]: everything, [IVY]: everything })
    const legacy: Row = { _id: nextId(), sessionId: ROOM, orgId: ORG, messageType: 'bot_response', requestedBy: DANA, content: 'From before labels.' }
    w.store.set(String(legacy._id), legacy)
    const ivys = answer(IVY, ['rec-b'], [{ id: String(legacy._id), ver: 'legacy', mode: 'full' }])
    await w.write(ivys)
    // The consent comes after Ivy's closure was kept: the share check stops at a version shared with everyone here.
    legacy.consent = { by: DANA, at: T0, ver: 'legacy', scope: 'everyone', how: 'earlier' }
    const expected = await walk.widenOK(ivys, String(ivys.labelVer), 'everyone', { deps: w.deps, session })
    expect(expected).to.deep.equal({ ok: true })
    expect(await widenOK(ivys, String(ivys.labelVer), 'everyone', { session })).to.deep.equal(expected)
  })

  describe('a share is for the chat it was given in, decided from kept closures too', () => {
    const FORK = 'cccccccccccccccccccccccc'
    /**
     * Dana's answer that read R, shared with everyone in the room, then Bob's room answer built on its text: its kept
     * closure holds Dana's as a share to check. Then a new process, so the reads below take the closures kept here.
     */
    const sharedThenBuiltOn = async (w: ReturnType<typeof world>): Promise<{ own: Row }> => {
      const shared = answer(DANA, ['rec-r'])
      shared.consent = { by: DANA, at: T0, ver: shared.labelVer, scope: 'everyone' }
      await w.write(shared)
      const own = answer(BOB, [], [parentOf(shared)])
      await w.write(own)
      resetVisibility()
      configureVisibility(w.deps)
      return { own }
    }

    it("read as Bob's fork will read it, his room answer built on Dana's shared one hides from him; read in the room it shows", async () => {
      const w = world({ [DANA]: everything, [BOB]: (id) => id !== 'rec-r' })
      const { own } = await sharedThenBuiltOn(w)
      const kept = w.kept.counts.kept
      const gets = w.kept.counts.gets
      const asFork = await visibleRows(reader(BOB), session, [own], { member: true, chatsRead: [FORK] })
      expect(asFork.get(String(own._id))).to.include({ verdict: 'hidden', reason: 'parent' })
      const asRoom = await visibleRows(reader(BOB), session, [own], { member: true })
      expect(asRoom.get(String(own._id))?.verdict).to.equal('visible')
      // Both decided by the closures kept at the writes: read back from the store, none made again.
      expect(w.kept.counts.gets).to.be.greaterThan(gets)
      expect(w.kept.counts.kept).to.equal(kept)
    })

    it("a verdict judged from kept closures as another chat's read is kept under a key of its own: never served to a read of the chat itself, nor the other way", async () => {
      const w = world({ [DANA]: everything, [BOB]: (id) => id !== 'rec-r' })
      const { own } = await sharedThenBuiltOn(w)
      // Verdicts are kept while the reader's permission epochs and the chats' audiences can be read.
      configureVisibility({
        epochs: { values: async (keys) => keys.map(() => '1') },
        audiences: async (_org, ids) => new Map(ids.map((id) => [id, 0])),
      })
      const put = sinon.spy(w.deps.store as VerdictStore, 'putMany')
      const verdictFor = async (chatsRead?: string[]): Promise<string | undefined> =>
        (await visibleRows(reader(BOB), session, [own], { member: true, ...(chatsRead && { chatsRead }) })).get(String(own._id))?.verdict
      try {
        expect(await verdictFor([FORK])).to.equal('hidden')
        expect(await verdictFor()).to.equal('visible')
        const asked = w.counts.access
        expect(await verdictFor([FORK])).to.equal('hidden')
        expect(await verdictFor()).to.equal('visible')
        // The second read of each is served from the verdict the first one kept: no access asked again.
        expect(w.counts.access).to.equal(asked)
        const keys = new Set(put.getCalls().flatMap((call) => call.args[0].map(([key]) => key)))
        expect(keys.size, 'one key for the fork read, one for the room read').to.equal(2)
      } finally {
        put.restore()
      }
    })
  })
})
