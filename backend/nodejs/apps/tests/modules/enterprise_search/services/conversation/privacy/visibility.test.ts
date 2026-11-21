import { expect } from 'chai'
import {
  configureVisibility,
  consentCovers,
  everyMemberOf,
  fullOK,
  hiddenRowFor,
  hiddenView,
  includableRows,
  memberSinceOf,
  pubVersion,
  readerView,
  resetVisibility,
  sealedRef,
  unlabelledAnswers,
  visibleRows,
  widenOK,
  withoutSteps,
  type Reader,
  type ReaderRow,
  type ReaderSession,
  type VisibilityDeps,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { VerdictStore } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/verdict-store'
import { labelContentHash } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label'
import { formatPreviousConversations } from '../../../../../../src/modules/enterprise_search/utils/utils'
import type { EpochSource } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/epochs'
import type { Readable } from '../../../../../../src/modules/enterprise_search/utils/viewer-redaction'

const ORG = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const ROOM = 'bbbbbbbbbbbbbbbbbbbbbbbb'
const DANA = 'd00000000000000000000001'
const CAROL = 'c00000000000000000000002'
const BOB = 'b00000000000000000000003'
const IVY = 'e00000000000000000000004'
const PRIYA = 'f00000000000000000000005'
const ZED = '900000000000000000000006'

const R = 'rec-R'
const R2 = 'rec-R2'

/** Who can open what, as the access service answers. */
const OPENS: Record<string, string[]> = {
  [DANA]: [R, R2],
  [CAROL]: [R, R2],
  [IVY]: [R],
  [BOB]: [],
  [PRIYA]: [],
  [ZED]: [],
}

const T0 = new Date('2026-10-01T10:00:00Z')
const T1 = new Date('2026-10-02T10:00:00Z')
const T2 = new Date('2026-10-03T10:00:00Z')

const session: ReaderSession = {
  _id: ROOM,
  orgId: ORG,
  userId: DANA,
  sharedWith: [
    { userId: CAROL, accessLevel: 'write', addedAt: T0 },
    { userId: BOB, accessLevel: 'write', addedAt: T0 },
    { userId: IVY, accessLevel: 'read', addedAt: T0 },
    { userId: PRIYA, accessLevel: 'write', addedAt: T2 },
  ],
}

const reader = (userId: string): Reader => ({ userId, orgId: ORG })

const label = (over: Record<string, unknown> = {}, ver = '', hash = ''): Record<string, unknown> => ({
  v: 5,
  ver,
  contentHash: hash,
  direct: {
    records: [],
    vrids: [],
    nodes: [],
    saVrids: [],
    personal: [],
    agents: [],
    chat: [],
    projects: [],
    marks: [],
    ...((over.direct as object | undefined) ?? {}),
  },
  parents: [],
  complete: true,
  tagset: 1,
  pub: false,
  ...Object.fromEntries(Object.entries(over).filter(([k]) => k !== 'direct')),
})

let n = 0
const nextId = (): string => (++n).toString(16).padStart(24, '0')

/** An answer row stored the way the save seals it: its label bound to its version and to the hash of its text. */
const answer = (
  content: string,
  over: Record<string, unknown> = {},
  labelOver: Record<string, unknown> | null = {},
  requestedBy = DANA,
): ReaderRow & Record<string, unknown> => {
  const labelVer = (over.labelVer as string | undefined) ?? nextId()
  const parts = [{ type: 'text', content }]
  const row: ReaderRow & Record<string, unknown> = {
    _id: nextId(),
    sessionId: ROOM,
    orgId: ORG,
    messageType: 'bot_response',
    requestedBy,
    content,
    parts,
    labelVer,
    createdAt: T1,
    ...over,
  }
  if (labelOver !== null) {
    row.provenance = label(labelOver, labelVer, labelContentHash(content, parts))
  }
  return row
}

const question = (content: string, authorUserId = DANA): ReaderRow & Record<string, unknown> => ({
  _id: nextId(),
  sessionId: ROOM,
  messageType: 'user_query',
  authorUserId,
  content,
  createdAt: T1,
})

interface World {
  deps: Partial<VisibilityDeps>
  asked: Array<{ userId: string; ids: string[] }>
  runsAsked: Array<{ sessionIds: string[]; runIds: string[] }>
  store: Map<string, ReaderRow>
  /** Each chat's stored audience epoch, which a share or its end moves on. */
  audience: Map<string, number>
  /** The ids of each read of the label store, in order. */
  labelReads: string[][]
}

/** A fake access service and label store over `rows`. */
const world = (
  rows: ReaderRow[],
  opts: { fail?: boolean; deleted?: string[]; agents?: Record<string, string[]>; service?: string[]; epochs?: EpochSource } = {},
): World => {
  const store = new Map<string, ReaderRow>(rows.map((r) => [String(r._id), r]))
  const asked: World['asked'] = []
  const runsAsked: World['runsAsked'] = []
  const audience = new Map<string, number>()
  const labelReads: string[][] = []
  const deps: Partial<VisibilityDeps> & Record<string, unknown> = {
    readable: async (who, ids) => {
      asked.push({ userId: who.userId, ids: [...ids] })
      if (opts.fail) {
        return { readable: new Set(), asked: new Set(), deleted: new Set(), failed: true, validUntil: Date.now() + 5000 }
      }
      const opens = new Set(OPENS[who.userId] ?? [])
      for (const d of opts.deleted ?? []) opens.delete(d)
      return {
        readable: new Set(ids.filter((id) => opens.has(id))),
        asked: new Set(ids),
        deleted: new Set(ids.filter((id) => (opts.deleted ?? []).includes(id))),
        failed: false,
        validUntil: Date.now() + 30_000,
      }
    },
    labels: async (_org, ids) => {
      labelReads.push([...ids])
      return new Map(ids.flatMap((id) => (store.has(id) ? [[id, store.get(id) as ReaderRow]] : [])))
    },
    audiences: async (_org: string, ids: readonly string[]) => new Map(ids.map((id) => [id, audience.get(id) ?? 0])),
    runs: async (_org, sessionIds, runIds) => {
      runsAsked.push({ sessionIds: [...sessionIds], runIds: [...runIds] })
      const wrote = (row: ReaderRow): boolean =>
        runIds.includes(row.runId as string) ||
        (Array.isArray(row.labelHistory) && row.labelHistory.some((e) => runIds.includes((e as { runId?: string }).runId as string)))
      return [...store.values()].filter((row) => sessionIds.includes(String(row.sessionId)) && wrote(row))
    },
    canUseAgent: async (who, key) => (opts.agents?.[key] ?? []).includes(who.userId),
    canReadConversation: async () => undefined,
    canReadProject: async () => undefined,
    isServiceAgent: async (_org, key) => (opts.service ?? []).includes(key),
    sharedText: (text) => text.replace(/\[[^\]]*\]\([^)]*\)/g, ''),
    // When this build first served (the boot stamp's record): after Bob and the others joined, before Priya did.
    switchAt: async () => T1.getTime(),
    labelRule: async () => true,
    store: new VerdictStore(),
    ...(opts.epochs && { epochs: opts.epochs }),
  }
  return { deps, asked, runsAsked, store, audience, labelReads }
}

const verdictsFor = async (who: string, rows: ReaderRow[], w: World, extra: Record<string, unknown> = {}) =>
  visibleRows(reader(who), session, rows, { deps: w.deps, member: true, ...extra })

describe('the reader check: who may see an answer', () => {
  afterEach(() => {
    resetVisibility()
  })

  describe('an answer is shown only to readers who can open everything its label names', () => {
    it("Dana's answer that read R: Dana and Carol see it, Bob and Priya do not", async () => {
      const a = answer('R says revenue grew 12%.', {}, { direct: { records: [R] } })
      const w = world([a])
      for (const [who, expected] of [
        [DANA, 'visible'],
        [CAROL, 'visible'],
        [BOB, 'hidden'],
        [PRIYA, 'hidden'],
      ] as const) {
        const v = (await verdictsFor(who, [a], w)).get(String(a._id))
        expect(v?.verdict, who).to.equal(expected)
        if (expected === 'hidden') expect(v?.reason).to.equal('sources')
      }
    })

    it('partial access is not enough: Ivy can open R but not R2', async () => {
      const a = answer('R and R2.', {}, { direct: { records: [R], nodes: [R2] } })
      const w = world([a])
      expect((await verdictsFor(IVY, [a], w)).get(String(a._id))?.verdict).to.equal('hidden')
      expect((await verdictsFor(CAROL, [a], w)).get(String(a._id))?.verdict).to.equal('visible')
    })

    it("an answer that used the asker's own tool is hers alone, even for Carol who can open everything else", async () => {
      const a = answer('From your tracker.', {}, { direct: { records: [R], personal: [DANA] } })
      const w = world([a])
      expect((await verdictsFor(DANA, [a], w)).get(String(a._id))?.verdict).to.equal('visible')
      const carol = (await verdictsFor(CAROL, [a], w)).get(String(a._id))
      expect(carol).to.include({ verdict: 'hidden', reason: 'personal' })
    })

    it('an answer that names nothing is shown to every member', async () => {
      const a = answer('Hello.', {}, { pub: true })
      const w = world([a])
      expect((await verdictsFor(BOB, [a], w)).get(String(a._id))?.verdict).to.equal('visible')
    })
  })

  describe('a label that cannot be relied on leaves the answer to its asker', () => {
    const cases: Array<[string, () => ReaderRow, string]> = [
      ['no label', () => answer('No label.', {}, null), 'label_missing'],
      ['an incomplete label', () => answer('Partly labelled.', {}, { complete: false, incomplete: ['untagged_prompt_part:tool'] }), 'label_incomplete'],
      ['a label of an unknown version', () => answer('Future.', {}, { v: 6 }), 'label_unknown_version'],
      ['a label with an unknown kind of source', () => answer('Future.', {}, { direct: { future: [] } }), 'label_unknown_version'],
      [
        'text changed behind its label',
        () => {
          const a = answer('Original.', {}, {})
          return { ...a, content: 'Changed by a write that bypassed the save.' }
        },
        'label_stale',
      ],
    ]
    for (const [what, make, reason] of cases) {
      it(`${what}: the asker sees it, Carol does not (${reason})`, async () => {
        // What is stored: the check reads the label and the text it is bound to from the store.
        const a = make()
        const w = world([a])
        expect((await verdictsFor(DANA, [a], w)).get(String(a._id))?.verdict).to.equal('visible')
        expect((await verdictsFor(CAROL, [a], w)).get(String(a._id))).to.include({ verdict: 'hidden', reason })
      })
    }

    it('the stored label is the one judged, never one a caller passes in', async () => {
      const stored = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([stored])
      const forged = { ...stored, provenance: label({ pub: true }, String(stored.labelVer), labelContentHash(String(stored.content), stored.parts)) }
      expect((await verdictsFor(BOB, [forged], w)).get(String(stored._id))?.verdict).to.equal('hidden')
    })
  })

  describe('every failed check hides', () => {
    it("a check that cannot answer hides Dana's answer from Carol as unchecked; Dana keeps her own, as the per-record view shows it", async () => {
      const a = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([a], { fail: true })
      expect((await verdictsFor(CAROL, [a], w)).get(String(a._id))).to.include({ verdict: 'unchecked', reason: 'check_failed' })
      expect((await verdictsFor(DANA, [a], w)).get(String(a._id))).to.include({ verdict: 'visible', basis: 'asker' })
    })

    describe("the asker keeps her own answers while the check can't run (as the per-record view shows them)", () => {
      const solo: ReaderSession = { _id: 'eeeeeeeeeeeeeeeeeeeeeeee', orgId: ORG, userId: DANA }
      const agentChat: ReaderSession = { _id: 'ffffffffffffffffffffffff', orgId: ORG, userId: DANA, agentKey: 'agent-1' }
      const perRecordView = async <M extends object>(rows: M[]): Promise<M[]> => rows

      it('in a solo chat, an agent chat and a room: shown to Dana, the note for everyone else', async () => {
        for (const [where, chat] of [['solo', solo], ['agent chat', agentChat], ['room', session]] as const) {
          const a = answer('Dana: R says revenue grew.', { sessionId: chat._id }, { direct: { records: [R], nodes: ['kb-dana'] } })
          const w = world([a], { fail: true })
          const dana = await readerView(reader(DANA), chat, [a], { deps: w.deps, member: true, perRecordView })
          expect((dana.rows[0] as { content: string }).content, where).to.equal('Dana: R says revenue grew.')
          expect(dana.verdicts.get(String(a._id)), where).to.include({ verdict: 'visible', basis: 'asker' })
        }
        const a = answer('Dana: R says revenue grew.', {}, { direct: { records: [R] } })
        const w = world([a], { fail: true })
        const carol = await readerView(reader(CAROL), session, [a], { deps: w.deps, member: true, perRecordView })
        expect((carol.rows[0] as { content: string }).content).to.equal("We couldn't confirm your access. Try again.")
      })

      it('an agent the check cannot settle is the asker\'s still; a check that ran and found her access gone still hides it', async () => {
        const agentAnswer = answer('Via the agent.', {}, { direct: { agents: [{ key: 'agent-1', kind: 'config' }] } })
        const w = world([agentAnswer])
        w.deps.canUseAgent = async () => undefined
        expect((await verdictsFor(DANA, [agentAnswer], w)).get(String(agentAnswer._id))).to.include({ verdict: 'visible', basis: 'asker' })
        expect((await verdictsFor(CAROL, [agentAnswer], w)).get(String(agentAnswer._id))?.verdict).to.equal('unchecked')
        const lost = answer('R3 says.', {}, { direct: { records: ['rec-R3'] } })
        const w2 = world([lost])
        expect((await verdictsFor(DANA, [lost], w2)).get(String(lost._id))).to.include({ verdict: 'hidden', reason: 'sources' })
      })

      it('a denial settled beside what could not be checked still hides it from her', async () => {
        const a = answer('R and R3.', {}, { direct: { records: [R, 'rec-R3'] } })
        const w = world([a])
        w.deps.readable = async (_who, ids) => ({ readable: new Set(), asked: new Set(ids.filter((id) => id === 'rec-R3')), deleted: new Set(), failed: true, validUntil: Date.now() + 5000 })
        expect((await verdictsFor(DANA, [a], w)).get(String(a._id))).to.include({ verdict: 'hidden', reason: 'sources' })
      })

      it("her own answer still goes through the per-record view, which decides as it did before labels", async () => {
        const a = answer('Dana: R says.', {}, { direct: { records: [R] } })
        const w = world([a], { fail: true })
        const seen: unknown[] = []
        const marking = async <M extends object>(rows: M[]): Promise<M[]> => {
          seen.push(...rows)
          return rows.map((r) => ({ ...r, answerHidden: { redacted: true, reason: 'access_check_unavailable' } }) as M)
        }
        const dana = await readerView(reader(DANA), session, [a], { deps: w.deps, member: true, perRecordView: marking, perRecordFor: unlabelledAnswers })
        expect(seen).to.have.length(1)
        expect((dana.rows[0] as { content: string }).content).to.equal("We couldn't confirm your access. Try again.")
      })

      it("her model is given her own answer while the check can't run", async () => {
        const a = answer('Dana: R says.', {}, { direct: { records: [R] } })
        const w = world([a], { fail: true })
        const rows = (await includableRows(reader(DANA), session, [a], { deps: w.deps })) as Array<Record<string, unknown>>
        expect(rows[0]).to.include({ content: 'Dana: R says.', _ref: String(a._id) })
      })
    })

    it('except the run that just answered, for its own asker: retrieval checked it', async () => {
      const a = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([a], { fail: true })
      const v = await visibleRows(reader(DANA), session, [a], { deps: w.deps, member: true, freshIds: [a._id] })
      expect(v.get(String(a._id))?.verdict).to.equal('visible')
      const carol = await visibleRows(reader(CAROL), session, [a], { deps: w.deps, member: true, freshIds: [a._id] })
      expect(carol.get(String(a._id))?.verdict).to.equal('unchecked')
    })

    it('an access service that throws hides', async () => {
      const a = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([a])
      w.deps.readable = () => Promise.reject(new Error('down'))
      expect((await verdictsFor(CAROL, [a], w)).get(String(a._id))?.verdict).to.equal('unchecked')
    })

    it('a label store that cannot be read leaves every answer to its asker', async () => {
      const a = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([a])
      w.deps.labels = () => Promise.reject(new Error('down'))
      expect((await verdictsFor(CAROL, [a], w)).get(String(a._id))).to.include({ verdict: 'hidden', reason: 'label_missing' })
      expect((await verdictsFor(DANA, [a], w)).get(String(a._id))?.verdict).to.equal('visible')
    })

    it('an agent nobody can check hides the answer', async () => {
      const a = answer('Agent answer.', {}, { direct: { agents: [{ key: 'agent-1', kind: 'config' }] } })
      const w = world([a])
      w.deps.canUseAgent = async () => undefined
      expect((await verdictsFor(CAROL, [a], w)).get(String(a._id))?.verdict).to.equal('unchecked')
    })
  })

  describe('a deleted source', () => {
    it("the asker keeps an answer whose only missing source was deleted; nobody else does", async () => {
      const a = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([a], { deleted: [R] })
      expect((await verdictsFor(DANA, [a], w)).get(String(a._id))?.verdict).to.equal('visible')
      expect((await verdictsFor(CAROL, [a], w)).get(String(a._id))).to.include({ verdict: 'hidden', reason: 'deleted' })
    })
  })

  describe('earlier answers an answer was given', () => {
    it("Dana's follow-up built on her answer that read R is hidden from Bob, though it names nothing itself", async () => {
      const a1 = answer('R says.', {}, { direct: { records: [R] } })
      const t1 = answer('As I said.', {}, { parents: [{ id: a1._id, ver: a1.labelVer, mode: 'text' }] })
      const w = world([a1, t1])
      expect((await verdictsFor(BOB, [t1], w)).get(String(t1._id))).to.include({ verdict: 'hidden', reason: 'parent' })
      expect((await verdictsFor(CAROL, [t1], w)).get(String(t1._id))?.verdict).to.equal('visible')
    })

    it('a parent is judged at the version the child was given: a later regenerate does not change it', async () => {
      const v0 = nextId()
      const oldLabel = label({ direct: { records: [R] } }, v0, 'old-hash')
      const a1 = answer('Now from the handbook.', { labelHistory: [{ ver: v0, provenance: oldLabel }] }, { pub: true })
      const t1 = answer('Built on the old one.', {}, { parents: [{ id: a1._id, ver: v0, mode: 'full' }] })
      const w = world([a1, t1])
      expect((await verdictsFor(BOB, [a1], w)).get(String(a1._id))?.verdict).to.equal('visible')
      expect((await verdictsFor(BOB, [t1], w)).get(String(t1._id))?.verdict).to.equal('hidden')
    })

    it('a parent whose version is gone hides the child from everyone but its asker', async () => {
      const a1 = answer('R says.', {}, { direct: { records: [R] } })
      const t1 = answer('Built on it.', {}, { parents: [{ id: a1._id, ver: 'no-such-version', mode: 'text' }] })
      const w = world([a1, t1])
      expect((await verdictsFor(CAROL, [t1], w)).get(String(t1._id))?.verdict).to.equal('hidden')
    })

    it('a parent row that no longer exists hides the child', async () => {
      const t1 = answer('Built on it.', {}, { parents: [{ id: nextId(), ver: nextId(), mode: 'text' }] })
      const w = world([t1])
      expect((await verdictsFor(CAROL, [t1], w)).get(String(t1._id))).to.include({ verdict: 'hidden', reason: 'parent' })
    })

    it('one access request covers a row and every version behind it', async () => {
      const a1 = answer('R says.', {}, { direct: { records: [R] } })
      const a2 = answer('R2 says.', {}, { direct: { records: [R2] }, parents: [{ id: a1._id, ver: a1.labelVer, mode: 'text' }] })
      const t1 = answer('Both.', {}, { parents: [{ id: a2._id, ver: a2.labelVer, mode: 'text' }] })
      const w = world([a1, a2, t1])
      await verdictsFor(CAROL, [t1], w)
      expect(w.asked).to.have.length(1)
      expect(w.asked[0]?.ids).to.have.members([R, R2])
    })
  })

  describe('a file the run read is judged by the answer whose run wrote it', () => {
    const FILE = 'art-board-summary'
    /** The parent a run's label names for a file it read or listed; `runId` is the run that wrote it. */
    const fileParent = (runId?: string) => ({ id: '', ver: '', mode: 'full', artifactId: FILE, ...(runId !== undefined && { runId }) })

    it('a later answer that only listed the file is shown to a reader who may see the answer that wrote it', async () => {
      const wrote = answer('Here is the checklist.', { runId: 'run-wrote' }, { pub: true })
      const later = answer('Anything else?', { runId: 'run-later' }, { parents: [fileParent('run-wrote')] }, CAROL)
      const w = world([wrote, later])
      expect((await verdictsFor(BOB, [later], w)).get(String(later._id))?.verdict).to.equal('visible')
      expect(w.runsAsked).to.deep.equal([{ sessionIds: [ROOM], runIds: ['run-wrote'] }])
    })

    it('it stays hidden from a reader the answer that wrote the file is hidden from, and is shown to one who can open it', async () => {
      const wrote = answer('R says.', { runId: 'run-wrote' }, { direct: { records: [R] } })
      const later = answer('Anything else?', { runId: 'run-later' }, { parents: [fileParent('run-wrote')] }, CAROL)
      const w = world([wrote, later])
      expect((await verdictsFor(BOB, [later], w)).get(String(later._id))).to.include({ verdict: 'hidden', reason: 'parent' })
      expect((await verdictsFor(IVY, [later], w)).get(String(later._id))?.verdict).to.equal('visible')
      expect((await verdictsFor(CAROL, [later], w)).get(String(later._id))?.verdict).to.equal('visible')
    })

    it("its asker loses it too when the answer that wrote the file is hidden from them: the model was given that file", async () => {
      const wrote = answer('Only Carol may read this.', { runId: 'run-wrote' }, { direct: { personal: [CAROL] } }, CAROL)
      const later = answer('Built on the file.', { runId: 'run-later' }, { parents: [fileParent('run-wrote')] }, DANA)
      const w = world([wrote, later])
      expect((await verdictsFor(DANA, [later], w)).get(String(later._id))).to.include({ verdict: 'hidden', reason: 'parent' })
    })

    it('after a regenerate or an edit, the file is judged at the version its run wrote, archived on the row', async () => {
      const first = nextId()
      const edited = nextId()
      const readNothing = label({ pub: true }, first, 'first-hash')
      const readR = label({ direct: { records: [R] } }, edited, 'edited-hash')
      // The row was answered three times: run-first (read nothing), run-edited (read R), and now run-now (read R).
      const wrote = answer(
        'The latest answer, from R.',
        { runId: 'run-now', labelHistory: [{ ver: first, runId: 'run-first', provenance: readNothing }, { ver: edited, runId: 'run-edited', provenance: readR }] },
        { direct: { records: [R] } },
      )
      const fromFirst = answer('Listed the first file.', {}, { parents: [fileParent('run-first')] }, CAROL)
      const fromEdited = answer('Listed the edited file.', {}, { parents: [fileParent('run-edited')] }, CAROL)
      const fromNow = answer('Listed the latest file.', {}, { parents: [fileParent('run-now')] }, CAROL)
      const w = world([wrote, fromFirst, fromEdited, fromNow])
      const bob = await verdictsFor(BOB, [fromFirst, fromEdited, fromNow], w)
      expect(bob.get(String(fromFirst._id))?.verdict).to.equal('visible')
      expect(bob.get(String(fromEdited._id))).to.include({ verdict: 'hidden', reason: 'parent' })
      expect(bob.get(String(fromNow._id))).to.include({ verdict: 'hidden', reason: 'parent' })
    })

    it('one access request covers the answer and the answer that wrote its file', async () => {
      const wrote = answer('R2 says.', { runId: 'run-wrote' }, { direct: { records: [R2] } })
      const later = answer('And R.', {}, { direct: { records: [R] }, parents: [fileParent('run-wrote')] }, CAROL)
      const w = world([wrote, later])
      await verdictsFor(IVY, [later], w)
      expect(w.asked).to.have.length(1)
      expect(w.asked[0]?.ids).to.have.members([R, R2])
    })

    describe('a writer that cannot be found hides the answer from everyone but its asker, as before', () => {
      it('a file parent naming no run (a label written before runs were named)', async () => {
        const wrote = answer('Here is the checklist.', { runId: 'run-wrote' }, { pub: true })
        const later = answer('Anything else?', {}, { parents: [fileParent()] }, CAROL)
        const w = world([wrote, later])
        expect((await verdictsFor(BOB, [later], w)).get(String(later._id))).to.include({ verdict: 'hidden', reason: 'parent' })
        expect((await verdictsFor(CAROL, [later], w)).get(String(later._id))?.verdict).to.equal('visible')
        expect(w.runsAsked).to.deep.equal([])
      })

      it('a run no answer of this chat wrote', async () => {
        const elsewhere = answer('In another chat.', { runId: 'run-wrote', sessionId: nextId() }, { pub: true })
        const later = answer('Anything else?', {}, { parents: [fileParent('run-wrote')] }, CAROL)
        const w = world([elsewhere, later])
        expect((await verdictsFor(BOB, [later], w)).get(String(later._id))).to.include({ verdict: 'hidden', reason: 'parent' })
        expect((await verdictsFor(CAROL, [later], w)).get(String(later._id))?.verdict).to.equal('visible')
      })

      it('a lookup that fails', async () => {
        const wrote = answer('Here is the checklist.', { runId: 'run-wrote' }, { pub: true })
        const later = answer('Anything else?', {}, { parents: [fileParent('run-wrote')] }, CAROL)
        const w = world([wrote, later])
        w.deps.runs = async () => {
          throw new Error('store down')
        }
        expect((await verdictsFor(BOB, [later], w)).get(String(later._id))).to.include({ verdict: 'hidden', reason: 'parent' })
      })

      it('a writer the store returns without the run asked for is not taken', async () => {
        const other = answer('Some other answer.', { runId: 'run-other' }, { pub: true })
        const later = answer('Anything else?', {}, { parents: [fileParent('run-wrote')] }, CAROL)
        const w = world([other, later])
        w.deps.runs = async () => [other]
        expect((await verdictsFor(BOB, [later], w)).get(String(later._id))).to.include({ verdict: 'hidden', reason: 'parent' })
      })
    })
  })

  describe('an answer shared with the reader', () => {
    it('a share with everyone shows Bob its text alone, and lets a follow-up that quoted it as text through', async () => {
      const a1 = answer('R says.', {}, { direct: { records: [R] } })
      a1.consent = { by: DANA, at: T1, ver: a1.labelVer, scope: 'everyone' }
      const t1 = answer('As shared.', {}, { parents: [{ id: a1._id, ver: a1.labelVer, mode: 'text' }] })
      const w = world([a1, t1])
      expect((await verdictsFor(BOB, [a1], w)).get(String(a1._id))?.verdict).to.equal('consented')
      expect((await verdictsFor(BOB, [t1], w)).get(String(t1._id))?.verdict).to.equal('visible')
    })

    it('a share covers only the version shared, and never a parent given in full', async () => {
      const a1 = answer('R says.', {}, { direct: { records: [R] } })
      a1.consent = { by: DANA, at: T1, ver: 'an-older-version', scope: 'everyone' }
      const w = world([a1])
      expect((await verdictsFor(BOB, [a1], w)).get(String(a1._id))?.verdict).to.equal('hidden')
      const a2 = answer('R says.', {}, { direct: { records: [R] } })
      a2.consent = { by: DANA, at: T1, ver: a2.labelVer, scope: 'everyone' }
      const t1 = answer('Full.', {}, { parents: [{ id: a2._id, ver: a2.labelVer, mode: 'full' }] })
      const w2 = world([a2, t1])
      expect((await verdictsFor(BOB, [t1], w2)).get(String(t1._id))?.verdict).to.equal('hidden')
    })

    it('a share with Bob reaches Bob, under the membership it was made for, and nobody else', async () => {
      const a1 = answer('R says.', {}, { direct: { records: [R] } })
      a1.consent = { by: DANA, at: T1, ver: a1.labelVer, scope: 'people' }
      a1.consentTo = [{ userId: BOB, memberSince: memberSinceOf(session, BOB) }]
      const w = world([a1])
      expect((await verdictsFor(BOB, [a1], w)).get(String(a1._id))?.verdict).to.equal('consented')
      expect((await verdictsFor(PRIYA, [a1], w)).get(String(a1._id))?.verdict).to.equal('hidden')
      // Bob removed and added again: a new membership, which the share was not made for.
      const readded = { ...session, sharedWith: [{ userId: BOB, accessLevel: 'write', addedAt: T2 }] }
      const again = await visibleRows(reader(BOB), readded, [a1], { deps: w.deps })
      expect(again.get(String(a1._id))?.verdict).to.equal('hidden')
    })

    it('a share never covers someone who is not a member now', () => {
      const a1 = answer('R says.', {}, { direct: { records: [R] } })
      a1.consent = { by: DANA, at: T1, ver: a1.labelVer, scope: 'everyone' }
      expect(consentCovers(a1, String(a1.labelVer), { userId: ZED, orgId: ORG })).to.equal(false)
      expect(consentCovers(a1, String(a1.labelVer), { userId: BOB, orgId: ORG, member: true })).to.equal(true)
    })

    it('a shared copy in the chat is text for its members', async () => {
      const copy = { _id: nextId(), sessionId: ROOM, messageType: 'shared_answer', authorUserId: DANA, requestedBy: DANA, content: 'Shared.' }
      const w = world([copy])
      expect((await verdictsFor(BOB, [copy], w)).get(copy._id)?.verdict).to.equal('consented')
    })
  })

  describe('answers written before labels', () => {
    it('while nothing records when the rules started for the chat, every member reads them as before', async () => {
      const a = answer('Before labels.', {}, null)
      delete a.labelVer
      const w = world([a])
      w.deps.switchAt = async () => undefined
      expect((await verdictsFor(PRIYA, [a], w)).get(String(a._id))?.verdict).to.equal('visible')
    })

    it('a switch time that cannot be read leaves them to their asker and the owner', async () => {
      const a = answer('Before labels.', {}, null, CAROL)
      delete a.labelVer
      const w = world([a])
      w.deps.switchAt = () => Promise.reject(new Error('down'))
      expect((await verdictsFor(BOB, [a], w)).get(String(a._id))?.verdict).to.equal('hidden')
      expect((await verdictsFor(DANA, [a], w)).get(String(a._id))?.verdict).to.equal('visible')
      expect((await verdictsFor(CAROL, [a], w)).get(String(a._id))?.verdict).to.equal('visible')
    })

    const legacy = (): ReaderRow & Record<string, unknown> => {
      const row = answer('Before labels.', {}, null)
      delete row.labelVer
      return row
    }

    it('its asker and those who could read the chat before labels started keep the per-record view', async () => {
      const a = legacy()
      const w = world([a])
      expect((await verdictsFor(DANA, [a], w)).get(String(a._id))?.verdict).to.equal('visible')
      expect((await verdictsFor(BOB, [a], w)).get(String(a._id))?.verdict).to.equal('visible')
    })

    it('someone added since is not one of them: the answer is its asker\'s alone for Priya', async () => {
      const a = legacy()
      const w = world([a])
      expect((await verdictsFor(PRIYA, [a], w)).get(String(a._id))).to.include({ verdict: 'hidden', reason: 'label_missing' })
    })

    describe('the switch is when the privacy rules started for the chat', () => {
      // T1 is when this build first served (the boot stamp's record). Priya is added at T2, after it.
      const PROJECT = '7a0000000000000000000007'
      /** An answer from before labels, written before any stamp below. */
      const old = (): ReaderRow & Record<string, unknown> => ({ ...legacy(), createdAt: new Date('2026-10-01T08:00:00Z') })
      const verdictIn = async (chat: ReaderSession, who: string, row: ReaderRow, w: World) =>
        (await visibleRows(reader(who), chat, [row], { deps: w.deps, member: true })).get(String(row._id))

      it('a chat stamped before the boot stamp recorded its time started then: someone added in between is not an earlier reader', async () => {
        const STAMPED_AT = new Date('2026-10-01T12:00:00Z')
        const between = new Date('2026-10-01T18:00:00Z')
        const chat: ReaderSession = {
          ...session,
          privacyStampedAt: STAMPED_AT,
          sharedWith: [
            { userId: BOB, accessLevel: 'write', addedAt: T0 },
            { userId: PRIYA, accessLevel: 'write', addedAt: between },
          ],
        }
        const a = old()
        const w = world([a])
        expect(await verdictIn(chat, PRIYA, a, w)).to.include({ verdict: 'hidden', reason: 'label_missing' })
        expect(await verdictIn(chat, BOB, a, w)).to.include({ verdict: 'visible' })
      })

      it('a chat stamped after the rules started still started with them: its stamp alone is not the switch', async () => {
        // Stamped a day after Priya joined (as when a chat first becomes a group chat after the upgrade).
        const chat: ReaderSession = { ...session, privacyStampedAt: new Date('2026-10-04T10:00:00Z') }
        const a = old()
        const w = world([a])
        expect(await verdictIn(chat, PRIYA, a, w)).to.include({ verdict: 'hidden', reason: 'label_missing' })
        expect(await verdictIn(chat, BOB, a, w)).to.include({ verdict: 'visible' })
      })

      it('before the boot stamp records its time, a chat\'s own stamp says when the rules started for it', async () => {
        const a = old()
        const w = world([a])
        w.deps.switchAt = async () => undefined
        const stamped: ReaderSession = { ...session, privacyStampedAt: T1 }
        expect(await verdictIn(stamped, PRIYA, a, w)).to.include({ verdict: 'hidden', reason: 'label_missing' })
        expect(await verdictIn(stamped, BOB, a, w)).to.include({ verdict: 'visible' })
      })

      describe('a reader the chat reaches through its project', () => {
        const inProject = (over: Partial<ReaderSession> = {}): ReaderSession => ({
          ...session,
          sharedWith: [],
          projectId: PROJECT,
          projectVisibility: 'project',
          ...over,
        })

        it('reads as before when the chat was visible to the project with no change recorded since', async () => {
          const a = old()
          const w = world([a])
          expect(await verdictIn(inProject(), ZED, a, w)).to.include({ verdict: 'visible' })
        })

        it('gets the rule after labels when the chat became visible to the project under the rules', async () => {
          const a = old()
          const w = world([a])
          expect(await verdictIn(inProject({ projectVisibleAt: T2 }), ZED, a, w)).to.include({ verdict: 'hidden', reason: 'label_missing' })
        })

        it('gets it too when that change was made before the boot stamp recorded its time', async () => {
          // Made visible between the build's start and its boot stamp's record, which is later than both.
          const a = old()
          const w = world([a])
          w.deps.switchAt = async () => T2.getTime()
          expect(await verdictIn(inProject({ projectVisibleAt: T1 }), ZED, a, w)).to.include({ verdict: 'hidden', reason: 'label_missing' })
          // The owner and her own share rows are not affected.
          expect(await verdictIn(inProject({ projectVisibleAt: T1 }), DANA, a, w)).to.include({ verdict: 'visible' })
        })

        it('a stored verdict is not served once the chat became visible to the project', async () => {
          const a = old()
          const w = world([a], { epochs: { values: async (keys) => keys.map(() => '1') } })
          expect(await verdictIn(inProject(), ZED, a, w)).to.include({ verdict: 'visible' })
          expect(await verdictIn(inProject({ projectVisibleAt: T2 }), ZED, a, w)).to.include({ verdict: 'hidden', reason: 'label_missing' })
        })
      })
    })

    describe('an answer built on ones from before labels', () => {
      const withSteps = (): ReaderRow & Record<string, unknown> => ({
        ...legacy(),
        parts: [{ type: 'tool_call', resultSummary: 'step' }, { type: 'text', content: 'Before labels.' }],
        referenceData: [{ name: 'R', metadata: { recordId: R } }],
      })

      it("the asker's own answer from before labels goes to her model as text where others read the chat, whole where only she does", async () => {
        // The room before Priya joined, so every member could read it then, and a turn that names no source.
        const earlier: ReaderSession = { ...session, sharedWith: (session.sharedWith as unknown[]).slice(0, 3) as never }
        const q = question('What happened before?')
        const a = { ...withSteps(), referenceData: [] }
        const w = world([q, a])
        const [, shared] = await includableRows(reader(DANA), earlier, [q, a], { deps: w.deps })
        expect(shared).to.include({ content: 'Before labels.', _ver: 'legacy' })
        expect((shared as Record<string, unknown>).parts).to.deep.equal([])
        const solo: ReaderSession = { _id: ROOM, orgId: ORG, userId: DANA, sharedWith: [] }
        const [, alone] = await includableRows(reader(DANA), solo, [q, a], { deps: w.deps })
        expect((alone as Record<string, unknown>).parts).to.have.length(2)
        const [, whole] = await includableRows(reader(DANA), solo, [q, withSteps()], { deps: world([q, withSteps()]).deps })
        expect((whole as Record<string, unknown>).referenceData, 'one that names a source, where only she reads').to.have.length(1)
      })

      const oldFile = (over: Record<string, unknown> = {}) => ({
        id: '',
        ver: '',
        mode: 'full',
        artifactId: 'art-board',
        conversationId: ROOM,
        writtenAt: T0.getTime(),
        ...over,
      })

      it('a file written before the rules started lets those who could read its chat then read an answer built on it', async () => {
        const built = answer('From the board file.', {}, { parents: [oldFile()] })
        const w = world([built])
        expect((await verdictsFor(BOB, [built], w)).get(String(built._id))?.verdict).to.equal('visible')
        expect((await verdictsFor(PRIYA, [built], w)).get(String(built._id))).to.include({ verdict: 'hidden', reason: 'parent' })
        expect((await verdictsFor(DANA, [built], w)).get(String(built._id))?.verdict).to.equal('visible')
      })

      it("a file written since, or one whose record said nothing of its chat or time, is its asker's alone", async () => {
        for (const parent of [oldFile({ writtenAt: T2.getTime() }), oldFile({ writtenAt: undefined }), oldFile({ conversationId: undefined })]) {
          const built = answer('From a file.', {}, { parents: [parent] })
          const w = world([built])
          expect((await verdictsFor(BOB, [built], w)).get(String(built._id)), JSON.stringify(parent)).to.include({ verdict: 'hidden', reason: 'parent' })
          expect((await verdictsFor(DANA, [built], w)).get(String(built._id))?.verdict).to.equal('visible')
        }
      })

      it('an answer built on one that read such a file is judged the same, through its closure: each later answer of an upgraded room', async () => {
        const first = answer('From the board file.', {}, { parents: [oldFile()] })
        const next = answer('And so the board says ship.', {}, { parents: [{ id: first._id, ver: first.labelVer, mode: 'text' }] }, CAROL)
        const after = answer('Shipping it.', {}, { parents: [{ id: next._id, ver: next.labelVer, mode: 'full' }] }, IVY)
        const w = world([first, next, after])
        const verdicts = await verdictsFor(BOB, [next, after], w)
        expect(verdicts.get(String(next._id))?.verdict).to.equal('visible')
        expect(verdicts.get(String(after._id))?.verdict).to.equal('visible')
        expect((await verdictsFor(PRIYA, [next], w)).get(String(next._id))).to.include({ verdict: 'hidden', reason: 'parent' })
      })

      it('two versions of one file are told apart by when each was written', async () => {
        const early = answer('From the board, as it was.', {}, { parents: [oldFile()] })
        const late = answer('From the board, as it is now.', {}, { parents: [oldFile({ writtenAt: T2.getTime() })] })
        const w = world([early, late])
        const verdicts = await verdictsFor(BOB, [early, late], w)
        expect(verdicts.get(String(early._id))?.verdict).to.equal('visible')
        expect(verdicts.get(String(late._id))).to.include({ verdict: 'hidden', reason: 'parent' })
      })

      it("a file of another chat is judged by that chat's own readers", async () => {
        const OTHER = '0c0000000000000000000009'
        const built = answer('From her other chat.', {}, { parents: [oldFile({ conversationId: OTHER })] })
        const w = world([built])
        w.deps.chats = async () =>
          new Map([[OTHER, { _id: OTHER, orgId: ORG, userId: DANA, sharedWith: [{ userId: CAROL, accessLevel: 'read', addedAt: T0 }] }]])
        expect((await verdictsFor(CAROL, [built], w)).get(String(built._id))?.verdict).to.equal('visible')
        expect((await verdictsFor(BOB, [built], w)).get(String(built._id))).to.include({ verdict: 'hidden', reason: 'parent' })
      })
    })

    describe('an answer with no label written after the chat took on the privacy rules', () => {
      // The chat became a group chat under the rules between T1 and T2. A build without the rules (an older replica
      // during a deploy) can still write an answer with no label after that: it is not an answer from before labels.
      const STAMPED_AT = new Date('2026-10-02T12:00:00Z')
      const stamped: ReaderSession = { ...session, privacyStampedAt: STAMPED_AT }
      const written = (at: Date, over: Record<string, unknown> = {}) => {
        const row = legacy()
        row.createdAt = at
        Object.assign(row, over)
        return row
      }
      const verdictIn = async (chat: ReaderSession, who: string, row: ReaderRow, w: World) =>
        (await visibleRows(reader(who), chat, [row], { deps: w.deps, member: true })).get(String(row._id))

      it('is its asker\'s alone: Bob, who read the chat before labels, does not see it', async () => {
        const a = written(T2)
        const w = world([a])
        expect(await verdictIn(stamped, BOB, a, w)).to.include({ verdict: 'hidden', reason: 'label_missing' })
        expect(await verdictIn(stamped, DANA, a, w)).to.include({ verdict: 'visible' })
      })

      it('an answer with no label written before the stamp keeps the view from before labels', async () => {
        const a = written(T1)
        const w = world([a])
        expect(await verdictIn(stamped, BOB, a, w)).to.include({ verdict: 'visible' })
      })

      it('a row read without its write time counts from its id: one made after the stamp is not from before labels', async () => {
        const after = written(T2, { _id: `${Math.floor(T2.getTime() / 1000).toString(16)}0000000000000001` })
        delete after.createdAt
        const before = written(T1, { _id: `${Math.floor(T1.getTime() / 1000).toString(16)}0000000000000002` })
        delete before.createdAt
        const w = world([after, before])
        expect(await verdictIn(stamped, BOB, after, w)).to.include({ verdict: 'hidden', reason: 'label_missing' })
        expect(await verdictIn(stamped, BOB, before, w)).to.include({ verdict: 'visible' })
      })

      it('a chat with no stamp keeps the view from before labels for its earlier readers', async () => {
        const a = written(T2)
        const w = world([a])
        expect(await verdictIn(session, BOB, a, w)).to.include({ verdict: 'visible' })
      })

      it("goes empty to Bob's model, and to its asker's where others read the chat: no member but her could read it; whole where only she does", async () => {
        const q = question('And then?')
        const a = written(T2)
        const w = world([q, a])
        const bob = await includableRows(reader(BOB), stamped, [q, a], { deps: w.deps })
        expect(bob[1]).to.include({ content: '' })
        const dana = await includableRows(reader(DANA), stamped, [q, a], { deps: w.deps })
        expect(dana[1]).to.include({ content: '' })
        expect(dana[1]).to.not.have.property('_ref')
        const alone = await includableRows(reader(DANA), { ...stamped, sharedWith: [] }, [q, a], { deps: w.deps })
        expect(alone[1]).to.include({ content: 'Before labels.' })
      })

      it('a row read with no id is shown to its asker alone', async () => {
        const a = written(T2)
        delete a._id
        const w = world([])
        const shown = await readerView(reader(BOB), stamped, [a], { deps: w.deps, member: true })
        expect((shown.rows[0] as { content?: unknown }).content).to.not.equal('Before labels.')
        const own = await readerView(reader(DANA), stamped, [a], { deps: w.deps, member: true })
        expect((own.rows[0] as { content?: unknown }).content).to.equal('Before labels.')
      })
    })
  })

  describe('a failed run, judged by the label it was sent with', () => {
    it('reaches those who can open what it read, and nobody else; a failure that read nothing reaches every member', async () => {
      const readR = answer('The model stopped.', { messageType: 'error' }, { direct: { records: [R] } })
      const plain = answer('The model is down.', { messageType: 'error' }, { pub: true })
      const unlabelled = answer('The model is down.', { messageType: 'error' }, null)
      const w = world([readR, plain, unlabelled])
      const carol = await verdictsFor(CAROL, [readR, plain, unlabelled], w)
      const bob = await verdictsFor(BOB, [readR, plain, unlabelled], w)
      expect(carol.get(String(readR._id))?.verdict).to.equal('visible')
      expect(bob.get(String(readR._id))?.verdict).to.equal('hidden')
      expect(bob.get(String(plain._id))?.verdict).to.equal('visible')
      expect(bob.get(String(unlabelled._id))?.verdict).to.equal('hidden')
      expect((await verdictsFor(DANA, [unlabelled], w)).get(String(unlabelled._id))?.verdict).to.equal('visible')
    })
  })

  describe('cards follow their answer; steps are their asker\'s alone', () => {
    it("Dana's card goes with her answer: Carol, who sees the answer, sees it; Bob does not", async () => {
      const q = question('What does R say?')
      const card = { _id: nextId(), sessionId: ROOM, messageType: 'tool_call', requestedBy: DANA, content: 'card' }
      const a = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([q, card, a])
      expect((await verdictsFor(DANA, [q, card, a], w)).get(card._id)?.verdict).to.equal('visible')
      expect((await verdictsFor(CAROL, [q, card, a], w)).get(card._id)?.verdict).to.equal('visible')
      const bob = await verdictsFor(BOB, [q, card, a], w)
      expect(bob.get(card._id)?.verdict).to.equal('hidden')
      expect(bob.get(String(q._id))?.verdict).to.equal('visible')
    })

    it('a card read without its answer is its asker\'s alone', async () => {
      const card = { _id: nextId(), sessionId: ROOM, messageType: 'tool_call', requestedBy: DANA, content: 'card' }
      const w = world([card])
      expect((await verdictsFor(DANA, [card], w)).get(card._id)?.verdict).to.equal('visible')
      expect((await verdictsFor(CAROL, [card], w)).get(card._id)?.verdict).to.equal('hidden')
    })

    it("another person's steps never reach a reader who sees the answer", async () => {
      const a = answer('R says.', { reasoning: [{ content: 'thinking' }], parts: [{ type: 'tool_call', resultSummary: 'step' }, { type: 'text', content: 'R says.' }] }, null)
      expect(JSON.stringify(withoutSteps(a))).to.not.include('thinking').and.not.include('step')
    })

    describe("a call waiting on its asker's approval card", () => {
      const ARGUMENT = 'TITLE-FROM-THE-BOARD-DECK'
      const EXPIRES = 1_900_000_000_000
      const waiting = (over: Record<string, unknown> = {}, approval: Record<string, unknown> = {}) => ({
        type: 'tool_call',
        toolCallId: 'call-1',
        toolName: 'mcp_jira_create_issue',
        args: JSON.stringify({ title: ARGUMENT }),
        resultPreview: `Waiting for your approval: ${ARGUMENT}`,
        status: 'awaiting_approval',
        approval: {
          approvalId: 'ap-1',
          instanceId: 'inst-1',
          serverName: 'Jira',
          toolName: 'create_issue',
          toolTitle: 'Create an issue',
          kind: 'write',
          readOnly: false,
          canRemember: true,
          expiresAt: EXPIRES,
          arguments: { title: ARGUMENT },
          ...approval,
        },
        ...over,
      })
      const STUB = {
        type: 'tool_call',
        toolCallId: 'call-1',
        stub: true,
        status: 'awaiting_approval',
        approval: { approvalId: 'ap-1', serverName: 'Jira', toolName: 'create_issue', toolTitle: 'Create an issue', expiresAt: EXPIRES },
      }
      const withParts = (...parts: unknown[]) =>
        answer('I will file it.\n\nWaiting for your approval to run create_issue on Jira.', { parts: [...parts, { type: 'text', content: 'I will file it.' }] }, null)

      it('reaches a reader of the answer as its tool, server and expiry, and nothing of the call', () => {
        const seen = withoutSteps(withParts(waiting())) as { parts: unknown[] }
        expect(seen.parts).to.deep.equal([STUB, { type: 'text', content: 'I will file it.' }])
        expect(JSON.stringify(seen.parts)).to.not.include(ARGUMENT).and.not.include('inst-1')
      })

      it("a sub-agent's card reaches them the same way, without the sub-agent's steps", () => {
        const sub = { type: 'sub_agent', runId: 'sub-1', roleName: 'filer', parts: [{ type: 'text', content: 'SUB-STEP' }, waiting()] }
        const seen = withoutSteps(withParts(sub)) as { parts: unknown[] }
        expect(seen.parts).to.deep.equal([STUB, { type: 'text', content: 'I will file it.' }])
        expect(JSON.stringify(seen.parts)).to.not.include('SUB-STEP').and.not.include('filer')
      })

      it('a restricted card reaches them without where it would send or what', () => {
        const restricted = waiting({}, { restricted: true, hosts: [{ host: 'kq7.example', unicode: 'kq7.example' }], argumentsPreview: ARGUMENT })
        const seen = withoutSteps(withParts(restricted)) as { parts: unknown[] }
        expect(seen.parts).to.deep.equal([STUB, { type: 'text', content: 'I will file it.' }])
        expect(JSON.stringify(seen.parts)).to.not.include('kq7').and.not.include(ARGUMENT)
      })

      it('a call settled on the reply that carried the turn on leaves nothing: the card’s own row says how it went', () => {
        for (const over of [
          { status: 'completed', approvalAnswer: 'allowed', approved: true },
          { status: 'blocked', approvalAnswer: 'declined' },
        ]) {
          const seen = withoutSteps(withParts(waiting(over))) as { parts: unknown[] }
          expect(seen.parts, JSON.stringify(over)).to.deep.equal([{ type: 'text', content: 'I will file it.' }])
        }
      })

      it('how the card stands comes from its row’s mark, or from its expiry as of now, with nothing written', () => {
        const at = new Date(EXPIRES - 60_000)
        for (const [approvalAnswered, now, status] of [
          [{ decision: 'approved', ran: true, at }, EXPIRES - 1, 'approved'],
          [{ decision: 'approved', ran: false, at }, EXPIRES - 1, 'not_run'],
          [{ decision: 'denied', ran: false, at }, EXPIRES + 1, 'denied'],
          [undefined, EXPIRES + 1, 'expired'],
          [undefined, EXPIRES - 1, 'awaiting_approval'],
        ] as const) {
          const row = { ...withParts(waiting()), ...(approvalAnswered && { approvalAnswered }) }
          const seen = withoutSteps(row, row, now) as { parts: Array<Record<string, unknown>> }
          expect(seen.parts[0], status).to.deep.equal({ ...STUB, status })
        }
      })

      it('a card without a tool, server or expiry, or another call, leaves nothing', () => {
        const noServer = waiting({}, { serverName: undefined })
        const noExpiry = waiting({}, { expiresAt: 'soon' })
        const step = { type: 'tool_call', toolCallId: 'call-2', toolName: 'knowledgehub__search', args: ARGUMENT, status: 'completed' }
        for (const part of [noServer, noExpiry, step]) {
          const seen = withoutSteps(withParts(part)) as { parts: unknown[] }
          expect(seen.parts, JSON.stringify(part)).to.deep.equal([{ type: 'text', content: 'I will file it.' }])
        }
      })
    })

    it("a card goes with its turn's answer when that answer is hidden from its own asker", async () => {
      const q = question('What does R3 say?')
      const card = { _id: nextId(), sessionId: ROOM, messageType: 'tool_call', requestedBy: DANA, content: 'card' }
      // A check that ran and found Dana's access to R3 gone: her answer hides from her, and its card with it.
      const a = answer('R3 says.', {}, { direct: { records: ['rec-R3'] } })
      const w = world([q, card, a])
      const dana = await verdictsFor(DANA, [q, card, a], w)
      expect(dana.get(String(a._id))?.verdict).to.equal('hidden')
      expect(dana.get(card._id)?.verdict).to.equal('hidden')
    })
  })

  describe('the verdict store', () => {
    const epochs = (value: { n: number }): EpochSource => ({
      values: async (keys) => keys.map(() => String(value.n)),
    })

    it('a second read is served from the store, and a permission change asks again', async () => {
      const a = answer('R says.', {}, { direct: { records: [R] } })
      const counter = { n: 1 }
      const w = world([a], { epochs: epochs(counter) })
      await verdictsFor(CAROL, [a], w)
      await verdictsFor(CAROL, [a], w)
      expect(w.asked).to.have.length(1)
      counter.n = 2
      await verdictsFor(CAROL, [a], w)
      expect(w.asked).to.have.length(2)
    })

    it('epochs that cannot be read store and serve nothing', async () => {
      const a = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([a], { epochs: { values: () => Promise.reject(new Error('down')) } })
      await verdictsFor(CAROL, [a], w)
      await verdictsFor(CAROL, [a], w)
      expect(w.asked).to.have.length(2)
    })

    it('a verdict is never served for text that changed behind its version', async () => {
      const a = answer('R says.', {}, { pub: true })
      const counter = { n: 1 }
      const w = world([a], { epochs: epochs(counter) })
      expect((await verdictsFor(BOB, [a], w)).get(String(a._id))?.verdict).to.equal('visible')
      const changed = { ...a, content: 'Something else.' }
      w.store.set(String(a._id), changed)
      expect((await verdictsFor(BOB, [changed], w)).get(String(a._id))?.verdict).to.equal('hidden')
    })

    it("an agent rule a read with no request behind it could not settle is settled again for a read that can", async () => {
      // Dana's answer, read by Carol: the asker's own answer passes an unsettled check anyway.
      const a = answer('The agent found three themes.', {}, { direct: { agents: [{ key: 'agent-k', kind: 'config' }] } })
      const w = world([a], { epochs: epochs({ n: 1 }) })
      // As the edition wires it: the agent check calls out as the reader, so a read with no request behind it cannot say.
      w.deps.canUseAgent = async (who, key) => (who.identity === undefined ? undefined : key === 'agent-k')
      const background = await visibleRows(reader(CAROL), session, [a], { deps: w.deps, member: true })
      expect(background.get(String(a._id))?.verdict).to.equal('unchecked')
      const asCaller = await visibleRows({ ...reader(CAROL), identity: { userId: CAROL } }, session, [a], { deps: w.deps, member: true })
      expect(asCaller.get(String(a._id))?.verdict).to.equal('visible')
    })
  })

  describe('a share or its end shows on the very next read: a stored verdict is keyed by what is stored', () => {
    const CANARY = 'HELIOTROPE-S-7731'
    const storing = (): EpochSource => ({ values: async (keys) => keys.map(() => '1') })
    type Row = ReaderRow & Record<string, unknown>

    /** A stored row as the detail read hands it to the check: the views took its share and label fields, and its place. */
    const asDetailShows = (row: Row): Row => {
      const shown = { ...row }
      for (const field of ['consent', 'consentTo', 'shareRequests', 'provenance', 'labelHistory', 'sessionId', 'orgId', 'seq', 'changeRev']) {
        delete shown[field]
      }
      return shown
    }
    const SHAPES: Array<[string, (row: Row) => Row]> = [
      ['rows passed as stored, as the feed passes them', (row) => ({ ...row })],
      ['rows passed as the detail read reshapes them', asDetailShows],
    ]

    /** What a share route writes on the stored row; `audience` moves the chat's audience epoch on, as the routes do. */
    const storeShare = (
      w: World,
      row: Row,
      share: { consent?: Record<string, unknown>; consentTo?: unknown[] },
      { audience = false }: { audience?: boolean } = {},
    ): Row => {
      const rest = { ...row }
      delete rest.consent
      delete rest.consentTo
      const next: Row = { ...rest, ...share }
      w.store.set(String(row._id), next)
      if (audience) w.audience.set(ROOM, (w.audience.get(ROOM) ?? 0) + 1)
      return next
    }

    const verdictOf = async (who: string, rows: Row[], w: World, id: unknown) => (await verdictsFor(who, rows, w)).get(String(id))

    for (const [shape, pass] of SHAPES) {
      describe(shape, () => {
        it("Stop sharing hides the answer from Bob on his next read, even if the chat's audience epoch did not move", async () => {
          const a = answer(`${CANARY} R says.`, {}, { direct: { records: [R] } })
          a.consent = { by: DANA, at: T1, ver: a.labelVer, scope: 'everyone' }
          const w = world([a], { epochs: storing() })
          expect((await verdictOf(BOB, [pass(a)], w, a._id))?.verdict).to.equal('consented')
          const stopped = storeShare(w, a, {})
          expect(await verdictOf(BOB, [pass(stopped)], w, a._id)).to.include({ verdict: 'hidden', reason: 'sources' })
        })

        it('Share with everyone shows its text at once to a reader who read it hidden a moment ago', async () => {
          const a = answer(`${CANARY} R says.`, {}, { direct: { records: [R] } })
          const w = world([a], { epochs: storing() })
          expect((await verdictOf(BOB, [pass(a)], w, a._id))?.verdict).to.equal('hidden')
          expect((await verdictOf(PRIYA, [pass(a)], w, a._id))?.verdict).to.equal('hidden')
          const shared = storeShare(w, a, { consent: { by: DANA, at: T2, ver: a.labelVer, scope: 'everyone' } })
          expect((await verdictOf(BOB, [pass(shared)], w, a._id))?.verdict).to.equal('consented')
          expect((await verdictOf(PRIYA, [pass(shared)], w, a._id))?.verdict).to.equal('consented')
        })

        it('a share with Bob by name shows him its text at once, and nobody else', async () => {
          const a = answer(`${CANARY} R says.`, {}, { direct: { records: [R] } })
          const w = world([a], { epochs: storing() })
          expect((await verdictOf(BOB, [pass(a)], w, a._id))?.verdict).to.equal('hidden')
          const shared = storeShare(w, a, {
            consent: { by: DANA, at: T2, ver: a.labelVer, scope: 'people' },
            consentTo: [{ userId: BOB, memberSince: memberSinceOf(session, BOB) }],
          })
          expect((await verdictOf(BOB, [pass(shared)], w, a._id))?.verdict).to.equal('consented')
          expect((await verdictOf(PRIYA, [pass(shared)], w, a._id))?.verdict).to.equal('hidden')
          const stopped = storeShare(w, shared, {})
          expect((await verdictOf(BOB, [pass(stopped)], w, a._id))?.verdict).to.equal('hidden')
        })

        it('who a share with people covers is read from the store: a change of the names alone reaches each of them at once', async () => {
          const a = answer(`${CANARY} R says.`, {}, { direct: { records: [R] } })
          const consent = { by: DANA, at: T2, ver: a.labelVer, scope: 'people' }
          a.consent = consent
          a.consentTo = [{ userId: BOB, memberSince: memberSinceOf(session, BOB) }]
          const w = world([a], { epochs: storing() })
          expect((await verdictOf(BOB, [pass(a)], w, a._id))?.verdict).to.equal('consented')
          expect((await verdictOf(PRIYA, [pass(a)], w, a._id))?.verdict).to.equal('hidden')
          const renamed = storeShare(w, a, { consent, consentTo: [{ userId: PRIYA, memberSince: memberSinceOf(session, PRIYA) }] })
          expect((await verdictOf(BOB, [pass(renamed)], w, a._id))?.verdict).to.equal('hidden')
          expect((await verdictOf(PRIYA, [pass(renamed)], w, a._id))?.verdict).to.equal('consented')
        })

        it("Carol's answer built on Dana's shared answer hides from Bob at once when Dana stops sharing", async () => {
          const a1 = answer(`${CANARY} R says.`, {}, { direct: { records: [R] } })
          a1.consent = { by: DANA, at: T1, ver: a1.labelVer, scope: 'everyone' }
          const t1 = answer('A two-line summary of it.', {}, { parents: [{ id: a1._id, ver: a1.labelVer, mode: 'text' }] }, CAROL)
          const w = world([a1, t1], { epochs: storing() })
          const before = await verdictsFor(BOB, [pass(a1), pass(t1)], w)
          expect(before.get(String(a1._id))?.verdict).to.equal('consented')
          expect(before.get(String(t1._id))?.verdict).to.equal('visible')
          const stopped = storeShare(w, a1, {}, { audience: true })
          const after = await verdictsFor(BOB, [pass(stopped), pass(t1)], w)
          expect(after.get(String(a1._id))?.verdict).to.equal('hidden')
          expect(after.get(String(t1._id))).to.include({ verdict: 'hidden', reason: 'parent' })
        })

        it('an answer built on a shared copy hides at once when the copy is removed', async () => {
          const copy: Row = { _id: nextId(), sessionId: ROOM, orgId: ORG, messageType: 'shared_answer', authorUserId: DANA, requestedBy: DANA, content: `${CANARY} shared.` }
          const t2 = answer('Built on the copy.', {}, { parents: [{ id: copy._id, ver: 'legacy', mode: 'text' }] }, CAROL)
          const w = world([copy, t2], { epochs: storing() })
          expect((await verdictOf(BOB, [pass(copy), pass(t2)], w, t2._id))?.verdict).to.equal('visible')
          // A removed copy leaves the page; the removal moves the chat's audience epoch on.
          w.store.set(String(copy._id), { ...copy, content: '', removedAt: T2 })
          w.audience.set(ROOM, (w.audience.get(ROOM) ?? 0) + 1)
          expect(await verdictOf(BOB, [pass(t2)], w, t2._id)).to.include({ verdict: 'hidden', reason: 'parent' })
        })

        it('while nothing changes, the next read is served from the store and asks nothing again', async () => {
          const a = answer(`${CANARY} R says.`, {}, { direct: { records: [R] } })
          a.consent = { by: DANA, at: T1, ver: a.labelVer, scope: 'everyone' }
          const w = world([a], { epochs: storing() })
          expect((await verdictOf(BOB, [pass(a)], w, a._id))?.verdict).to.equal('consented')
          expect((await verdictOf(BOB, [pass(a)], w, a._id))?.verdict).to.equal('consented')
          expect(w.asked).to.have.length(1)
        })
      })
    }

    it("an error shown to others as a fixed line is keyed by its stored text, not the line", async () => {
      const failed = answer('R was unreachable.', { messageType: 'error' }, { pub: true })
      const w = world([failed], { epochs: storing() })
      const shown = { ...asDetailShows(failed), content: "This question couldn't be answered. Only the person who asked can see why." }
      expect((await verdictOf(BOB, [shown], w, failed._id))?.verdict).to.equal('visible')
      // The stored text changed behind its version: the label no longer vouches for it.
      w.store.set(String(failed._id), { ...failed, content: `${CANARY} R says the budget is 4.2M.` })
      expect(await verdictOf(BOB, [shown], w, failed._id)).to.include({ verdict: 'hidden', reason: 'label_stale' })
    })

    it('readerView after Stop sharing: Bob gets the fixed hidden view, never the text', async () => {
      const a = answer(`${CANARY} R says.`, {}, { direct: { records: [R] } })
      a.consent = { by: DANA, at: T1, ver: a.labelVer, scope: 'everyone' }
      const w = world([a], { epochs: storing() })
      // The shared text as the app gives it, so a share that still held would carry the canary.
      configureVisibility({ sharedText: (text) => text })
      const perRecordView = async <M extends object>(rows: M[]): Promise<M[]> => rows
      const read = async (row: Row) =>
        (await readerView(reader(BOB), session, [asDetailShows(row)], { deps: w.deps, member: true, perRecordView })).rows[0]
      expect(JSON.stringify(await read(a))).to.include(CANARY)
      const stopped = storeShare(w, a, {}, { audience: true })
      const after = await read(stopped)
      expect(JSON.stringify(after)).to.not.include(CANARY)
      expect(after).to.deep.include({ content: 'This answer used content you can no longer access.' })
    })

    it("a chat audience epoch that cannot be read stores and serves nothing", async () => {
      const a = answer(`${CANARY} R says.`, {}, { direct: { records: [R] } })
      a.consent = { by: DANA, at: T1, ver: a.labelVer, scope: 'everyone' }
      const w = world([a], { epochs: storing() })
      ;(w.deps as Record<string, unknown>).audiences = () => Promise.reject(new Error('down'))
      await verdictOf(BOB, [asDetailShows(a)], w, a._id)
      const stopped = storeShare(w, a, {})
      expect((await verdictOf(BOB, [asDetailShows(stopped)], w, a._id))?.verdict).to.equal('hidden')
      expect(w.asked).to.have.length(2)
    })

    it('rows with no answer among them read no epoch, no audience epoch and no label', async () => {
      const epochReads: string[][] = []
      const w = world([], { epochs: { values: async (keys) => (epochReads.push([...keys]), keys.map(() => '1')) } })
      let audienceReads = 0
      ;(w.deps as Record<string, unknown>).audiences = async () => ((audienceReads += 1), new Map())
      const rows = [question('What does R say?'), question('Thanks.', CAROL), { ...question('FYI'), messageType: 'note' }]
      const verdicts = await verdictsFor(BOB, rows, w)
      expect([...verdicts.values()].map((v) => v.verdict)).to.deep.equal(['visible', 'visible', 'visible'])
      expect({ epochs: epochReads.length, audiences: audienceReads, labels: w.labelReads.length }).to.deep.equal({ epochs: 0, audiences: 0, labels: 0 })
    })

    it('with epochs but no audience reader wired, nothing is stored: every read asks again', async () => {
      const a = answer(`${CANARY} R says.`, {}, { direct: { records: [R] } })
      const w = world([a], { epochs: storing() })
      delete (w.deps as Record<string, unknown>).audiences
      await verdictOf(BOB, [a], w, a._id)
      await verdictOf(BOB, [a], w, a._id)
      expect(w.asked).to.have.length(2)
    })

    it('the stored label fields are read once per check: the key and the judge share the read', async () => {
      const a = answer(`${CANARY} R says.`, {}, { direct: { records: [R] } })
      const w = world([a], { epochs: storing() })
      await verdictOf(BOB, [asDetailShows(a)], w, a._id)
      expect(w.labelReads.filter((ids) => ids.includes(String(a._id)))).to.have.length(1)
      w.labelReads.length = 0
      await verdictOf(BOB, [asDetailShows(a)], w, a._id)
      expect(w.labelReads.filter((ids) => ids.includes(String(a._id))).length).to.be.at.most(1)
      expect(w.asked).to.have.length(1)
    })
  })

  describe("a question that answers a card is judged with the card, and the card follows its own run's answer", () => {
    const CARD_TEXT = 'Which figure from the board deck should lead: CANARY-R-4.2M or 3.9M?'
    const SELECTIONS = `User selections:\n1. "${CARD_TEXT}" → CANARY-R-4.2M`
    const WITHHELD_TEXT = 'Answered a question from the assistant.'
    type Row = ReaderRow & Record<string, unknown>
    const perRecordView = async <M extends object>(rows: M[]): Promise<M[]> => rows

    /** Dana's run that read R and stopped on a card (run-1), her answer to the card, and the run it resumed (run-2). */
    const cardTurn = () => {
      const asked: Row = { ...question('@assistant which figure should lead the deck?'), runId: 'run-1' }
      const card: Row = {
        _id: nextId(),
        sessionId: ROOM,
        orgId: ORG,
        messageType: 'tool_call',
        requestedBy: DANA,
        runId: 'run-1',
        inReplyTo: asked._id,
        content: '',
        tools: [{ toolName: 'ask_user_question', toolResult: { questions: [{ question: CARD_TEXT }] } }],
      }
      const waiting = answer('', { runId: 'run-1', inReplyTo: asked._id, status: 'waiting_input' }, { direct: { records: [R] } })
      const reply: Row = { ...question(SELECTIONS), runId: 'run-2', answersCard: card._id }
      const resumed = answer('It leads with the larger figure.', { runId: 'run-2', inReplyTo: reply._id }, { direct: { records: [R] } })
      return { asked, card, waiting, reply, resumed, rows: [asked, card, waiting, reply, resumed] }
    }
    const view = async (who: string, rows: Row[], w: World) =>
      (await readerView(reader(who), session, rows, { deps: w.deps, member: true, perRecordView })).rows as Row[]
    const byId = (rows: Row[], id: unknown) => rows.find((r) => String(r._id) === String(id)) as Row

    it("Bob, who can't read the card, gets Dana's answer to it without its selections; Carol and Dana get it whole", async () => {
      const t = cardTurn()
      const w = world(t.rows)
      const bob = await view(BOB, t.rows, w)
      expect(byId(bob, t.reply._id).content).to.equal(WITHHELD_TEXT)
      expect(JSON.stringify(bob)).to.not.include('CANARY-R')
      expect(byId(await view(CAROL, t.rows, w), t.reply._id).content).to.equal(SELECTIONS)
      expect(byId(await view(DANA, t.rows, w), t.reply._id).content).to.equal(SELECTIONS)
    })

    it("Dana's own model, where others read the chat, gets her card whole: its questions go with its run's answer, sealed", async () => {
      // A card carries no label of its own; it goes with the answer of its run, which was written under the rules.
      const t = cardTurn()
      const w = world(t.rows)
      const danas = (await includableRows(reader(DANA), session, [t.asked, t.card, t.waiting], { deps: w.deps })) as Row[]
      expect(byId(danas, t.card._id)).to.not.have.property('_withheld')
      expect(JSON.stringify(byId(danas, t.card._id).tools)).to.include(CARD_TEXT)
      expect(byId(danas, t.waiting._id)).to.include({ _ref: String(t.waiting._id), _ver: t.waiting.labelVer })
    })

    it("a card of a turn from before labels, where others read the chat, goes no further than that turn's answer", async () => {
      const asked: Row = { ...question('@assistant which figure should lead the deck?'), createdAt: T0 }
      const card: Row = { _id: nextId(), sessionId: ROOM, orgId: ORG, messageType: 'tool_call', content: '', createdAt: T0, tools: [{ toolName: 'ask_user_question', toolResult: { questions: [{ question: CARD_TEXT }] } }] }
      // Dana's answer from before labels names R: a run others read is given nothing of it, nor of its card.
      const old: Row = { _id: nextId(), sessionId: ROOM, orgId: ORG, messageType: 'bot_response', content: 'It leads with CANARY-R-4.2M.', referenceData: [{ id: R, recordId: R, name: R }], createdAt: T0 }
      const rows = [asked, card, old]
      const w = world(rows)
      const room = (await includableRows(reader(DANA), session, rows, { deps: w.deps })) as Row[]
      expect(byId(room, card._id)).to.include({ _withheld: true })
      expect(JSON.stringify(byId(room, card._id))).to.not.include(CARD_TEXT)
      const alone = (await includableRows(reader(DANA), { ...session, sharedWith: [] }, rows, { deps: w.deps })) as Row[]
      expect(JSON.stringify(byId(alone, card._id).tools), 'in a chat of her own the card goes whole').to.include(CARD_TEXT)
    })

    it("Bob's model gets the same line in its place; Carol's gets the selections", async () => {
      const t = cardTurn()
      const w = world(t.rows)
      const bobs = (await includableRows(reader(BOB), session, t.rows, { deps: w.deps })) as Row[]
      expect(byId(bobs, t.reply._id).content).to.equal(WITHHELD_TEXT)
      expect(JSON.stringify(bobs)).to.not.include('CANARY-R')
      const carols = (await includableRows(reader(CAROL), session, t.rows, { deps: w.deps })) as Row[]
      expect(byId(carols, t.reply._id).content).to.equal(SELECTIONS)
    })

    it("a feed page in change order: the card is judged by its own run's answer, never by another turn's", async () => {
      const t = cardTurn()
      // Dana's earlier answer, public, written again after the card: the page holds it and the card, no question between.
      const earlier = answer('Hello, all.', { runId: 'run-0' }, { pub: true })
      const w = world([earlier, ...t.rows])
      const page = [t.card, earlier, t.reply]
      const bob = await verdictsFor(BOB, page, w)
      expect(bob.get(String(earlier._id))?.verdict).to.equal('visible')
      expect(bob.get(String(t.card._id))?.verdict).to.equal('hidden')
      expect(byId(await view(BOB, page, w), t.reply._id).content).to.equal(WITHHELD_TEXT)
      expect((await verdictsFor(CAROL, page, w)).get(String(t.card._id))?.verdict).to.equal('visible')
    })

    it('an answer to a card read without its card (a feed page of one row) is judged by that card, read from the store', async () => {
      const t = cardTurn()
      const w = world(t.rows)
      expect(byId(await view(BOB, [t.reply], w), t.reply._id).content).to.equal(WITHHELD_TEXT)
      expect(byId(await view(CAROL, [t.reply], w), t.reply._id).content).to.equal(SELECTIONS)
    })

    it("a card whose run left no answer is its asker's alone, and so is what answers it", async () => {
      const t = cardTurn()
      const w = world([t.asked, t.card, t.reply])
      expect((await verdictsFor(CAROL, [t.asked, t.card, t.reply], w)).get(String(t.card._id))?.verdict).to.equal('hidden')
      expect(byId(await view(CAROL, [t.asked, t.card, t.reply], w), t.reply._id).content).to.equal(WITHHELD_TEXT)
      expect(byId(await view(DANA, [t.asked, t.card, t.reply], w), t.reply._id).content).to.equal(SELECTIONS)
    })

    it('an answer to a card stored before the link existed is judged with the card before it; with none read, its author keeps it alone', async () => {
      const t = cardTurn()
      const legacy: Row = { ...t.reply, _id: nextId() }
      delete legacy.answersCard
      const rows = [t.asked, t.card, t.waiting, legacy]
      const w = world(rows)
      expect(byId(await view(BOB, rows, w), legacy._id).content).to.equal(WITHHELD_TEXT)
      expect(byId(await view(CAROL, rows, w), legacy._id).content).to.equal(SELECTIONS)
      expect(byId(await view(CAROL, [legacy], w), legacy._id).content).to.equal(WITHHELD_TEXT)
      expect(byId(await view(DANA, [legacy], w), legacy._id).content).to.equal(SELECTIONS)
    })

    it("a share of the card's answer shows its text alone, never the card or what answers it", async () => {
      const t = cardTurn()
      t.waiting.consent = { by: DANA, at: T1, ver: t.waiting.labelVer, scope: 'everyone' }
      const w = world(t.rows)
      const bob = await verdictsFor(BOB, t.rows, w)
      expect(bob.get(String(t.waiting._id))?.verdict).to.equal('consented')
      expect(bob.get(String(t.card._id))?.verdict).to.equal('hidden')
      expect(byId(await view(BOB, t.rows, w), t.reply._id).content).to.equal(WITHHELD_TEXT)
    })
  })

  describe('a share is for the chat it was given in, never a fork of it', () => {
    const FORK = 'cccccccccccccccccccccccc'
    const THREAD = 'dddddddddddddddddddddddd'
    const fork: ReaderSession = { _id: FORK, orgId: ORG, userId: BOB, sharedWith: [{ userId: ZED, accessLevel: 'read', addedAt: T2 }] }
    const perRecordView = async <M extends object>(rows: M[]): Promise<M[]> => rows
    /** Dana's answer that read R, shared with everyone in the room, and Bob's answer built on its text, copied into his fork. */
    const forked = () => {
      const shared = answer('CANARY-N7 R says revenue grew.', {}, { direct: { records: [R] } })
      shared.consent = { by: DANA, at: T1, ver: shared.labelVer, scope: 'everyone' }
      const copy = answer('Bob: so revenue grew.', { sessionId: FORK }, { parents: [{ id: shared._id, ver: shared.labelVer, mode: 'text' }] }, BOB)
      return { shared, copy }
    }

    it("Zed, in Bob's fork only, does not read Bob's answer built on Dana's shared one", async () => {
      const { shared, copy } = forked()
      const w = world([shared, copy])
      const zed = await visibleRows(reader(ZED), fork, [copy], { deps: w.deps, member: true })
      expect(zed.get(String(copy._id))).to.include({ verdict: 'hidden', reason: 'parent' })
      // In the room the share still covers its members.
      const bobInRoom = await visibleRows(reader(BOB), session, [{ ...copy, sessionId: ROOM }], { deps: w.deps, member: true })
      expect(bobInRoom.get(String(copy._id))?.verdict).to.equal('visible')
    })

    it("a reply in the fork keeps no words of Dana's shared answer for Zed", async () => {
      const { shared } = forked()
      const reply = { ...question('And this?', BOB), sessionId: FORK, replyTo: { messageId: shared._id, kind: 'answer', excerpt: 'CANARY-N7 R says revenue grew.', authorUserId: DANA } }
      const w = world([shared, reply])
      const zed = await readerView(reader(ZED), fork, [reply], { deps: w.deps, member: true, perRecordView })
      expect(JSON.stringify(zed.rows)).to.not.include('CANARY-N7')
      expect((zed.rows[0] as { replyTo: { withheld?: boolean } }).replyTo.withheld).to.equal(true)
    })

    it("Dana's shared answer itself is not shared with the fork's members", async () => {
      const { shared } = forked()
      const w = world([shared])
      expect((await visibleRows(reader(ZED), fork, [shared], { deps: w.deps, member: true })).get(String(shared._id))?.verdict).to.equal('hidden')
    })

    it("a share still covers the room's private threads: Bob's thread answer built on it stays his", async () => {
      const { shared } = forked()
      const own = answer('Bob, privately: revenue grew.', { sessionId: THREAD }, { parents: [{ id: shared._id, ver: shared.labelVer, mode: 'text' }] }, BOB)
      const w = world([shared, own])
      const thread: ReaderSession = { _id: THREAD, orgId: ORG, userId: BOB, parentId: ROOM }
      const bob = await visibleRows(reader(BOB), thread, [own], { deps: w.deps, member: true })
      expect(bob.get(String(own._id))?.verdict).to.equal('visible')
    })

    it("read as the fork will read it, Bob's room answer built on Dana's shared one hides from Bob too", async () => {
      const { shared } = forked()
      const own = answer('Bob: so revenue grew.', {}, { parents: [{ id: shared._id, ver: shared.labelVer, mode: 'text' }] }, BOB)
      const w = world([shared, own])
      const asFork = await visibleRows(reader(BOB), session, [own], { deps: w.deps, member: true, chatsRead: [FORK] })
      expect(asFork.get(String(own._id))).to.include({ verdict: 'hidden', reason: 'parent' })
      const asRoom = await visibleRows(reader(BOB), session, [own], { deps: w.deps, member: true })
      expect(asRoom.get(String(own._id))?.verdict).to.equal('visible')
    })

    it("a verdict judged as another chat's read is never served to a read of the chat itself", async () => {
      const { shared } = forked()
      const own = answer('Bob: so revenue grew.', {}, { parents: [{ id: shared._id, ver: shared.labelVer, mode: 'text' }] }, BOB)
      const w = world([shared, own], { epochs: { values: async (keys) => keys.map(() => '1') } })
      expect((await verdictsFor(BOB, [own], w, { chatsRead: [FORK] })).get(String(own._id))?.verdict).to.equal('hidden')
      expect((await verdictsFor(BOB, [own], w)).get(String(own._id))?.verdict).to.equal('visible')
      expect((await verdictsFor(BOB, [own], w, { chatsRead: [FORK] })).get(String(own._id))?.verdict).to.equal('hidden')
    })
  })

  describe('fullOK', () => {
    it('judges one version of one row for one reader', async () => {
      const a = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([a])
      expect(await fullOK(reader(CAROL), a, String(a.labelVer), { deps: w.deps })).to.equal(true)
      expect(await fullOK(reader(BOB), a, String(a.labelVer), { deps: w.deps })).to.equal(false)
    })
  })

  describe('widenOK: what an asker may share', () => {
    it("Dana may share her answer that read R: she can open it", async () => {
      const a = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([a])
      expect(await widenOK(a, String(a.labelVer), 'everyone', { deps: w.deps })).to.deep.equal({ ok: true })
    })

    it('an answer whose label cannot be relied on cannot be shared', async () => {
      const a = answer('Partly labelled.', {}, { complete: false, incomplete: ['x'] })
      const w = world([a])
      expect(await widenOK(a, String(a.labelVer), 'everyone', { deps: w.deps })).to.include({ ok: false, code: 'SHARE_NOT_WIDENABLE' })
    })

    it('an answer built on one shared only with the asker cannot be shared, at any depth', async () => {
      const carols = answer('R2 says.', {}, { direct: { records: [R2] } }, CAROL)
      carols.consent = { by: CAROL, at: T1, ver: carols.labelVer, scope: 'people' }
      carols.consentTo = [{ userId: BOB, memberSince: memberSinceOf(session, BOB) }]
      const bobs = answer('Building on it.', {}, { parents: [{ id: carols._id, ver: carols.labelVer, mode: 'text' }] }, BOB)
      const w = world([carols, bobs])
      expect(await widenOK(bobs, String(bobs.labelVer), 'everyone', { deps: w.deps })).to.include({ ok: false, code: 'SHARE_NOT_WIDENABLE' })
    })

    it('an answer built on two that share an earlier one is shared: an answer reached twice is no loop', async () => {
      // Dana's answer, Bob's built on it, and Carol's given both: Dana's is reached directly and through Bob's.
      const danas = answer('The board met.', {}, { pub: true })
      const bobs = answer('So it moved.', {}, { parents: [{ id: danas._id, ver: danas.labelVer, mode: 'text' }] }, BOB)
      const carols = answer('Tell them.', {}, {
        parents: [{ id: danas._id, ver: danas.labelVer, mode: 'text' }, { id: bobs._id, ver: bobs.labelVer, mode: 'text' }],
      }, CAROL)
      const w = world([danas, bobs, carols])
      expect(await widenOK(carols, String(carols.labelVer), 'everyone', { deps: w.deps })).to.deep.equal({ ok: true })
    })

    it('a label that names its own row as an earlier one is still refused', async () => {
      const loop = answer('Round.', {}, {})
      ;(loop.provenance as { parents: unknown[] }).parents = [{ id: loop._id, ver: loop.labelVer, mode: 'text' }]
      const w = world([loop])
      expect(await widenOK(loop, String(loop.labelVer), 'everyone', { deps: w.deps })).to.include({ ok: false, code: 'SHARE_NOT_WIDENABLE' })
    })

    it('a parent shared with everyone in this chat lets a follow-up be shared in the chat, never outside it', async () => {
      const a1 = answer('R says.', {}, { direct: { records: [R] } }, CAROL)
      a1.consent = { by: CAROL, at: T1, ver: a1.labelVer, scope: 'everyone' }
      const bobs = answer('Building on it.', {}, { parents: [{ id: a1._id, ver: a1.labelVer, mode: 'text' }] }, BOB)
      const w = world([a1, bobs])
      expect((await widenOK(bobs, String(bobs.labelVer), 'everyone', { deps: w.deps })).ok).to.equal(true)
      expect(await widenOK(bobs, String(bobs.labelVer), 'outside', { deps: w.deps })).to.include({ ok: false, code: 'SHARE_NOT_WIDENABLE' })
    })

    it("a service agent's answer, or one built on it, is never shared", async () => {
      const sa = answer('From the agent.', {}, { direct: { agents: [{ key: 'svc', kind: 'service' }], saVrids: ['v1'] } })
      const t1 = answer('Building on it.', {}, { parents: [{ id: sa._id, ver: sa.labelVer, mode: 'text' }] })
      const w = world([sa, t1])
      expect(await widenOK(sa, String(sa.labelVer), 'everyone', { deps: w.deps })).to.include({ code: 'SHARE_NOT_ALLOWED_AGENT' })
      expect(await widenOK(t1, String(t1.labelVer), 'everyone', { deps: w.deps })).to.include({ code: 'SHARE_NOT_ALLOWED_AGENT' })
      const guest = answer('A guest agent answered.', { respondingAgentKey: 'svc' }, {})
      const w2 = world([guest], { service: ['svc'] })
      expect(await widenOK(guest, String(guest.labelVer), 'everyone', { deps: w2.deps })).to.include({ code: 'SHARE_NOT_ALLOWED_AGENT' })
    })

    it('a share with people names those who cannot open the sources', async () => {
      const a = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([a])
      const result = await widenOK(a, String(a.labelVer), { userIds: [BOB, CAROL] }, { deps: w.deps, session })
      expect(result).to.deep.equal({ ok: true, cannotOpen: [BOB] })
    })
  })

  describe('what a hidden row keeps', () => {
    it('only its place: no text, length, sources, steps, model or feedback', () => {
      const a = answer('R says revenue grew 12%.', {
        seq: 4,
        changeRev: 9,
        modelInfo: { modelKey: 'm' },
        feedback: [{ isHelpful: true }],
        citations: [{ citationId: 'c1' }],
        followUpQuestions: [{ question: 'More on R?' }],
        respondingAgentKey: 'agent-1',
        runId: 'run-1',
      }, { direct: { records: [R] } })
      const view = hiddenView(a)
      expect(view).to.have.all.keys(
        '_id', 'sessionId', 'messageType', 'content', 'contentFormat', 'citations', 'attachments', 'referenceData',
        'followUpQuestions', 'reasoning', 'tools', 'parts', 'seq', 'changeRev', 'createdAt', 'requestedBy',
      )
      expect(view.content).to.equal('')
    })

    it('a failed run looks like any other hidden answer to everyone but its asker', () => {
      const failed = { ...answer('Error quoting R.'), messageType: 'error', errorCode: 'X' }
      const shown = hiddenRowFor(failed, { verdict: 'hidden', reason: 'sources' })
      expect(shown.messageType).to.equal('bot_response')
      expect(shown.content).to.not.include('R')
      expect(shown).to.not.have.property('errorCode')
    })
  })

  describe('readerView: rows as the reader is given them', () => {
    it("Bob gets Dana's hidden answer as the fixed view, Carol gets it without Dana's steps", async () => {
      const q = question('What does R say?')
      const a = answer('R says.', { reasoning: [{ content: 'thinking about R' }], parts: [{ type: 'tool_call', resultSummary: 'R-step' }, { type: 'text', content: 'R says.' }] }, { direct: { records: [R] } })
      // The label was sealed over the stored parts.
      a.provenance = label({ direct: { records: [R] } }, String(a.labelVer), labelContentHash(String(a.content), a.parts))
      const w = world([q, a])
      const perRecordView = async <M extends object>(rows: M[]): Promise<M[]> => rows
      const bob = await readerView(reader(BOB), session, [q, a], { deps: w.deps, member: true, perRecordView })
      expect(bob.rows[1]).to.deep.include({ content: 'This answer used content you can no longer access.' })
      expect(JSON.stringify(bob.rows[1])).to.not.include('R-step')
      const carol = await readerView(reader(CAROL), session, [q, a], { deps: w.deps, member: true, perRecordView })
      expect(JSON.stringify(carol.rows[1])).to.include('R says.')
      expect(JSON.stringify(carol.rows[1])).to.not.include('R-step')
      expect(JSON.stringify(carol.rows[1])).to.not.include('thinking about R')
      const dana = await readerView(reader(DANA), session, [q, a], { deps: w.deps, member: true, perRecordView })
      expect(JSON.stringify(dana.rows[1])).to.include('R-step')
    })

    it("another person's quoted excerpt is left out; a reply's excerpt shows only to readers of the answer it quotes", async () => {
      const a = answer('R says revenue grew.', {}, { direct: { records: [R] } })
      const inline = question('Quoted excerpt:\n"""\nR says revenue grew.\n"""\n\nWhy?', CAROL)
      const reply = { ...question('And this?', CAROL), replyTo: { messageId: a._id, kind: 'answer', excerpt: 'R says revenue grew.', authorUserId: DANA } }
      const w = world([a, inline, reply])
      const perRecordView = async <M extends object>(rows: M[]): Promise<M[]> => rows
      const bob = await readerView(reader(BOB), session, [inline, reply], { deps: w.deps, member: true, perRecordView })
      expect(JSON.stringify(bob.rows)).to.not.include('revenue grew')
      expect((bob.rows[0] as { content: string }).content).to.equal('Why?')
      const carol = await readerView(reader(CAROL), session, [inline, reply], { deps: w.deps, member: true, perRecordView })
      expect((carol.rows[1] as { replyTo: { excerpt: string } }).replyTo.excerpt).to.equal('R says revenue grew.')
    })

    it("a reply's excerpt of an answer shared with the reader is the share's text of it; a reader of the whole answer keeps it", async () => {
      const a = answer('R says revenue grew.', {}, { direct: { records: [R] } })
      a.consent = { by: DANA, at: T1, ver: a.labelVer, scope: 'everyone' }
      // A stored excerpt is the answer's own text, so it can hold what the share takes out.
      const excerpt = 'R says revenue grew [chart](https://img.example.test/p.png?d=CANARY-REPLY).'
      const reply = { ...question('And this?', CAROL), replyTo: { messageId: a._id, kind: 'answer', excerpt, authorUserId: DANA } }
      const w = world([a, reply])
      // The reply check reads the share's text from the wiring, as `consentedView` does.
      configureVisibility({ sharedText: w.deps.sharedText })
      const perRecordView = async <M extends object>(rows: M[]): Promise<M[]> => rows
      const bob = await readerView(reader(BOB), session, [reply], { deps: w.deps, member: true, perRecordView })
      expect((bob.rows[0] as { replyTo: Record<string, unknown> }).replyTo).to.include({ excerpt: 'R says revenue grew .' })
      expect(JSON.stringify(bob.rows)).to.not.include('CANARY-REPLY')
      const carol = await readerView(reader(CAROL), session, [reply], { deps: w.deps, member: true, perRecordView })
      expect((carol.rows[0] as { replyTo: { excerpt: string } }).replyTo.excerpt).to.equal(excerpt)
    })

    it("a reply's excerpt of a shared answer is withheld when the share's text cannot be made", async () => {
      const a = answer('R says revenue grew.', {}, { direct: { records: [R] } })
      a.consent = { by: DANA, at: T1, ver: a.labelVer, scope: 'everyone' }
      const reply = { ...question('And this?', CAROL), replyTo: { messageId: a._id, kind: 'answer', excerpt: 'R says revenue grew.', authorUserId: DANA } }
      const w = world([a, reply])
      configureVisibility({ sharedText: (): string => { throw new Error('share text unavailable') } })
      const perRecordView = async <M extends object>(rows: M[]): Promise<M[]> => rows
      const bob = await readerView(reader(BOB), session, [reply], { deps: w.deps, member: true, perRecordView })
      expect((bob.rows[0] as { replyTo: Record<string, unknown> }).replyTo).to.include({ excerpt: '', withheld: true })
    })

    it('with no endpoint to ask, an answer that names a record hides, though its label let it through', async () => {
      const a = answer('R says [1].', { citations: [{ citationId: { _id: 'c1', metadata: { recordId: R } } }] }, { direct: { records: [R] } })
      const w = world([a])
      const view = await readerView(reader(DANA), session, [a], { deps: w.deps, member: true })
      expect((view.rows[0] as { answerHidden?: unknown }).answerHidden).to.deep.equal({ redacted: true, reason: 'access_check_unavailable' })
      const plain = answer('Nothing named.', {}, { pub: true })
      const w2 = world([plain])
      const shown = await readerView(reader(BOB), session, [plain], { deps: w2.deps, member: true })
      expect((shown.rows[0] as { content?: unknown }).content).to.equal('Nothing named.')
    })
  })

  describe('includableRows: what the sender\'s model is given', () => {
    it("Dana's own earlier answer goes to her model, sealed with its row and version", async () => {
      const q = question('What does R say?')
      const a = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([q, a])
      const rows = await includableRows(reader(DANA), session, [q, a], { deps: w.deps })
      expect(rows[1]).to.include({ content: 'R says.', _ref: a._id, _ver: a.labelVer })
      expect(rows[0]).to.include({ _ref: q._id, _ver: 'legacy' })
    })

    it("Dana's answer goes empty to Bob's model, and to Carol's unless she replied to it", async () => {
      const q = question('What does R say?')
      const a = answer('R says.', { tools: [{ toolName: 't', toolResult: 'R raw' }] }, { direct: { records: [R] } })
      const w = world([q, a])
      const bob = await includableRows(reader(BOB), session, [q, a], { deps: w.deps })
      expect(bob[1]).to.include({ content: '' })
      expect(bob[1]).to.not.have.property('_ref')
      const carol = await includableRows(reader(CAROL), session, [q, a], { deps: w.deps })
      expect(carol[1]).to.include({ content: '' })
      const replied = await includableRows(reader(CAROL), session, [q, a], { deps: w.deps, replyTargetId: String(a._id) })
      expect(replied[1]).to.include({ content: 'R says.', _ref: a._id })
      expect((replied[1] as { tools: unknown[] }).tools).to.deep.equal([])
    })

    it("another person's answer that restricts nobody goes as text", async () => {
      const a = answer('Hello.', {}, { pub: true })
      const w = world([a])
      const rows = await includableRows(reader(BOB), session, [a], { deps: w.deps })
      expect(rows[0]).to.include({ content: 'Hello.', _ref: a._id })
    })

    it("an answer Dana lost access to goes empty to her own model", async () => {
      const a = answer('R2 says.', {}, { direct: { records: [R2] } })
      const w = world([a])
      w.deps.readable = async (_who, ids) => ({ readable: new Set(), asked: new Set(ids), deleted: new Set(), failed: false, validUntil: Date.now() + 1000 })
      const rows = await includableRows(reader(DANA), session, [a], { deps: w.deps })
      expect(rows[0]).to.include({ content: '' })
    })

    it("another person's quoted excerpt never reaches the sender's model", async () => {
      const inline = question('Quoted excerpt:\n"""\nR says revenue grew.\n"""\n\nWhy?', DANA)
      const w = world([inline])
      const rows = await includableRows(reader(BOB), session, [inline], { deps: w.deps })
      expect(rows[0]).to.include({ content: 'Why?' })
    })

    describe("an answer shared with the sender enters their history as the share's text, however they could see it", () => {
      const TEXT = 'R says revenue grew [chart](https://img.example.test/p.png?d=CANARY-URL).'
      // What the share shows: the world's `sharedText` takes the link out.
      const SHARED_TEXT = 'R says revenue grew .'
      type Row = ReaderRow & Record<string, unknown>
      const shared = (scope: 'everyone' | 'people', to: string[] = []): Row => {
        const a = answer(TEXT, { tools: [{ toolName: 't', toolResult: 'R raw' }] }, { direct: { records: [R] } })
        a.consent = { by: DANA, at: T1, ver: a.labelVer, scope }
        if (scope === 'people') a.consentTo = to.map((userId) => ({ userId, memberSince: memberSinceOf(session, userId) }))
        return a
      }
      const given = async (who: string, rows: Row[], w: World) => (await includableRows(reader(who), session, rows, { deps: w.deps })) as Row[]

      it("Carol, who can open R too, is given Dana's shared answer as the share's text, sealed", async () => {
        const a = shared('everyone')
        const [row] = await given(CAROL, [a], world([a]))
        expect(row).to.include({ content: SHARED_TEXT, _ref: String(a._id), _ver: a.labelVer })
        expect(row?.tools).to.deep.equal([])
      })

      it('Bob, who sees it only through the share, is given the same text: no image, no address', async () => {
        const a = shared('everyone')
        const [row] = await given(BOB, [a], world([a]))
        expect(row).to.include({ content: SHARED_TEXT, _ref: String(a._id), _ver: a.labelVer })
        expect(JSON.stringify(row)).to.not.include('CANARY-URL')
      })

      it('a share with Bob by name reaches his model; Carol, not named, gets it empty as before', async () => {
        const a = shared('people', [BOB])
        const w = world([a])
        expect((await given(BOB, [a], w))[0]).to.include({ content: SHARED_TEXT, _ref: String(a._id) })
        const [carols] = await given(CAROL, [a], w)
        expect(carols).to.include({ content: '' })
        expect(carols).to.not.have.property('_ref')
      })

      it("Bob's turn gets the share's text though the history filter before the check emptied his copy of the answer", async () => {
        // The per-record view empties an answer whose sources the sender can't open; the share is why he may have it.
        const a = shared('people', [BOB])
        const emptied: Row = { ...a, content: '', answerHidden: { redacted: true, reason: 'no_longer_accessible' } }
        const [row] = await given(BOB, [emptied], world([a]))
        expect(row).to.include({ content: SHARED_TEXT, _ref: String(a._id), _ver: a.labelVer })
        expect(row?.tools).to.deep.equal([])
      })

      it('a share covers the version it was made for: once the answer is written again, his turn gets nothing of it', async () => {
        const a = shared('people', [BOB])
        const newer: Row = { ...a, labelVer: nextId(), content: 'R says revenue grew 15%.' }
        newer.provenance = label({ direct: { records: [R] } }, newer.labelVer as string, labelContentHash(newer.content as string, newer.parts as never))
        const [row] = await given(BOB, [{ ...newer, content: '' }], world([newer]))
        expect(row).to.include({ content: '' })
        expect(row).to.not.have.property('_ref')
      })

      it('the stored row decides: passed without its share it still enters as the text; passed with one the store lacks, empty', async () => {
        const a = shared('everyone')
        const shown: Row = { ...a }
        delete shown.consent
        expect((await given(CAROL, [shown], world([a])))[0]).to.include({ content: SHARED_TEXT })
        const plain = answer(TEXT, {}, { direct: { records: [R] } })
        const forged = { ...plain, consent: { by: DANA, at: T1, ver: plain.labelVer, scope: 'everyone' } }
        expect((await given(CAROL, [forged], world([plain])))[0]).to.include({ content: '' })
      })
    })
  })

  describe('an answer built on one from before labels', () => {
    // Dana's room: Carol, Bob and Ivy joined before labels started (T0 < T1), Priya after (T2). Dana asked there
    // before labels; her old answer has no label. Carol then asks, and her new answer is given Dana's old one as text.
    const THREAD = 'dddddddddddddddddddddddd'
    let seqN = 0
    const legacy = (content: string, records: string[] = [], over: Record<string, unknown> = {}): ReaderRow & Record<string, unknown> => ({
      _id: nextId(),
      sessionId: ROOM,
      orgId: ORG,
      seq: ++seqN,
      messageType: 'bot_response',
      content,
      referenceData: records.map((id) => ({ id, recordId: id, name: id })),
      createdAt: T0,
      ...over,
    })
    const builtOn = (old: ReaderRow, asker = CAROL, mode = 'text', over: Record<string, unknown> = {}) =>
      answer('So the plan holds.', { seq: ++seqN, ...over }, { parents: [{ id: String(old._id), ver: 'legacy', mode }] }, asker)
    /** The per-record view: an answer naming a record the reader cannot open is withheld whole. */
    const mainView = (calls: string[] = []): Partial<VisibilityDeps> => ({
      perRecordView: async (reader, rows) => {
        calls.push(reader.userId)
        const opens = new Set(OPENS[reader.userId] ?? [])
        return rows.map((m) => {
          const named = ((m as { referenceData?: Array<{ recordId?: string }> }).referenceData ?? []).map((r) => r.recordId)
          return named.every((id) => id !== undefined && opens.has(id)) ? m : { ...m, answerHidden: { redacted: true, reason: 'no_longer_accessible' } }
        })
      },
    })
    const legacyWorld = (rows: ReaderRow[], over: Partial<VisibilityDeps> = {}): World => {
      const w = world(rows)
      Object.assign(w.deps, {
        rowsUpTo: async (_org: string, sessionId: string, seq: number, limit: number) =>
          rows
            .filter((r) => r.sessionId === sessionId && (r.seq as number) <= seq)
            .sort((a, b) => (b.seq as number) - (a.seq as number))
            .slice(0, limit),
        chats: async (_org: string, ids: readonly string[]) => new Map(ids.filter((id) => id === ROOM).map((id) => [id, session])),
        ...mainView(),
        ...over,
      })
      return w
    }
    const verdict = async (who: string, row: ReaderRow, w: World, on: ReaderSession = session) =>
      (await visibleRows(reader(who), on, [row], { deps: w.deps, member: true })).get(String(row._id))?.verdict

    it('a reader who could read the room before labels reads it while the per-record view still shows them the old answer', async () => {
      const old = legacy('Old: R says the launch moved.', [R])
      const fresh = builtOn(old)
      const w = legacyWorld([old, fresh])
      expect(await verdict(CAROL, fresh, w), 'Carol, its asker, can open R').to.equal('visible')
      expect(await verdict(DANA, fresh, w), 'Dana asked the old answer').to.equal('visible')
      expect(await verdict(BOB, fresh, w), 'Bob cannot open R').to.equal('hidden')
      expect(await verdict(PRIYA, fresh, w), 'Priya joined after labels started').to.equal('hidden')
    })

    it('partial access is not enough, and an old answer naming nothing passes for every earlier reader', async () => {
      const both = legacy('Old: R and R2.', [R, R2])
      const onBoth = builtOn(both, DANA)
      const plain = legacy('Old: nothing named.')
      const onPlain = builtOn(plain, DANA)
      const w = legacyWorld([both, onBoth, plain, onPlain])
      expect(await verdict(IVY, onBoth, w), 'Ivy can open R, not R2').to.equal('hidden')
      expect(await verdict(CAROL, onBoth, w)).to.equal('visible')
      expect(await verdict(BOB, onPlain, w)).to.equal('visible')
      expect(await verdict(PRIYA, onPlain, w)).to.equal('hidden')
    })

    it("the asker's next turn is given her own answer built on the old one", async () => {
      const old = legacy('Old: R says the launch moved.', [R])
      const q = question('So what now?', CAROL)
      const fresh = builtOn(old)
      const w = legacyWorld([old, q, fresh])
      const rows = await includableRows(reader(CAROL), session, [old, q, fresh], { deps: w.deps })
      expect(rows[2]).to.include({ content: 'So the plan holds.', _ref: fresh._id, _ver: fresh.labelVer })
    })

    it("a private thread's answer built on its room's old answer: read by the room's share rows", async () => {
      const old = legacy('Old: R says the launch moved.', [R])
      const thread: ReaderSession = { _id: THREAD, orgId: ORG, userId: CAROL, parentId: ROOM }
      const fresh = builtOn(old, CAROL, 'text', { sessionId: THREAD })
      const w = legacyWorld([old, fresh])
      expect(await verdict(CAROL, fresh, w, thread)).to.equal('visible')
      const strangers = legacyWorld([old, fresh], { chats: async () => new Map([[ROOM, { ...session, sharedWith: [] }]]) })
      expect(await verdict(CAROL, fresh, strangers, thread), 'no share row of hers in the room').to.equal('hidden')
    })

    it('fails closed: a turn, a chat or a record check that cannot be read leaves the answer unchecked', async () => {
      const old = legacy('Old: R says the launch moved.', [R])
      // Ivy's answer, read by Carol: a check that cannot answer hides it from her.
      const fresh = builtOn(old, IVY)
      const thread: ReaderSession = { _id: THREAD, orgId: ORG, userId: CAROL, parentId: ROOM }
      const inThread = builtOn(old, CAROL, 'text', { sessionId: THREAD })
      const failing = (over: Partial<VisibilityDeps>) => legacyWorld([old, fresh, inThread], over)
      expect(await verdict(CAROL, fresh, failing({ rowsUpTo: async () => { throw new Error('store down') } }))).to.equal('unchecked')
      expect(await verdict(CAROL, fresh, failing({ rowsUpTo: async () => [] }))).to.equal('unchecked')
      expect(await verdict(CAROL, fresh, failing({
        perRecordView: async (_r, rows) => rows.map((m) => ({ ...m, answerHidden: { redacted: true, reason: 'access_check_unavailable' } })),
      }))).to.equal('unchecked')
      // Carol's own answer in her thread: a check that cannot answer leaves it hers, as the per-record view shows it.
      expect(await verdict(CAROL, inThread, failing({ chats: async () => { throw new Error('store down') } }), thread)).to.equal('visible')
      expect(await verdict(CAROL, inThread, failing({ chats: async () => new Map() }), thread), 'the room is gone').to.equal('hidden')
    })

    it('a turn the store has not read by the read’s deadline leaves the answer unchecked, at the deadline', async () => {
      const old = legacy('Old: R says the launch moved.', [R])
      const fresh = builtOn(old, IVY)
      const w = legacyWorld([old, fresh], { rowsUpTo: () => new Promise(() => undefined) })
      const started = Date.now()
      const verdicts = await visibleRows(reader(CAROL), session, [fresh], { deps: w.deps, member: true, timeoutMs: 50 })
      expect(verdicts.get(String(fresh._id))?.verdict).to.equal('unchecked')
      expect(Date.now() - started).to.be.lessThan(1_000)
    })

    it('never through the old view: a row written under the rules, a full parent, or one labelled since', async () => {
      const late = legacy('Late: written by an older build after the room took on the rules.', [], { createdAt: T2 })
      const onLate = builtOn(late)
      const roomStamped: ReaderSession = { ...session, privacyStampedAt: T1 }
      const stamped = legacyWorld([late, onLate])
      expect(await verdict(CAROL, onLate, stamped, roomStamped)).to.equal('hidden')
      expect(await verdict(CAROL, onLate, stamped), 'control: the same row in a room never stamped').to.equal('visible')
      const old = legacy('Old: nothing named.')
      const full = builtOn(old, CAROL, 'full')
      expect(await verdict(BOB, full, legacyWorld([old, full])), 'tool data never reached Bob').to.equal('hidden')
      const relabelled = legacy('New text.', [], { labelVer: nextId(), labelHistory: [{ ver: 'legacy' }] })
      const onOld = builtOn(relabelled)
      expect(await verdict(BOB, onOld, legacyWorld([relabelled, onOld]))).to.equal('hidden')
    })

    it("an old solo chat shared since: its owner keeps her new answer; who joined before labels reads it too", async () => {
      const SOLO = 'cccccccccccccccccccccccc'
      const solo = (addedAt: Date): ReaderSession => ({ _id: SOLO, orgId: ORG, userId: DANA, sharedWith: [{ userId: BOB, accessLevel: 'write', addedAt }] })
      const old = legacy('Old: nothing named.', [], { sessionId: SOLO })
      const fresh = answer('Next.', { sessionId: SOLO, seq: ++seqN }, { parents: [{ id: String(old._id), ver: 'legacy', mode: 'text' }] }, DANA)
      const w = legacyWorld([old, fresh], { chats: async () => new Map() })
      expect(await verdict(DANA, fresh, w, solo(T2))).to.equal('visible')
      expect(await verdict(BOB, fresh, w, solo(T2)), 'shared with Bob after labels started').to.equal('hidden')
      expect(await verdict(BOB, fresh, w, solo(T0)), 'shared with Bob before').to.equal('visible')
      const withTools = answer('Next.', { sessionId: SOLO, seq: ++seqN }, { parents: [{ id: String(old._id), ver: 'legacy', mode: 'full' }] }, DANA)
      const wt = legacyWorld([old, withTools], { chats: async () => new Map() })
      expect(await verdict(DANA, withTools, wt, solo(T0))).to.equal('visible')
      expect(await verdict(BOB, withTools, wt, solo(T0)), 'her tool data never reached Bob').to.equal('hidden')
    })
  })

  describe('a row withheld from the sender reaches the AI backend unsealed, so no answer is built on it', () => {
    // Bob asked first, and his answer read a file only he can open. Dana asks next.
    const BOBS_FILE = 'rec-bobs-file'
    const bobsWorld = (rows: ReaderRow[]): World => {
      const w = world(rows)
      w.deps.readable = async (who, ids) => {
        const opens = new Set(who.userId === BOB ? [BOBS_FILE] : (OPENS[who.userId] ?? []))
        return { readable: new Set(ids.filter((id) => opens.has(id))), asked: new Set(ids), deleted: new Set(), failed: false, validUntil: Date.now() + 30_000 }
      }
      return w
    }
    const bobsTurn = () => {
      const q = question('What does my file say?', BOB)
      const a = answer("Bob's file says the launch moved.", {}, { direct: { records: [BOBS_FILE] } }, BOB)
      return [q, a] as const
    }
    const sealedAnswers = (sent: Array<Record<string, unknown>>) =>
      sent.filter((r) => r.role === 'bot_response' && typeof r._ref === 'string').map((r) => [r._ref, r._ver])

    it("Bob's answer goes to Dana's model empty and marked withheld, and the formatter does not seal it again", async () => {
      const [q, b] = bobsTurn()
      const w = bobsWorld([q, b])

      const rows = await includableRows(reader(DANA), session, [q, b], { deps: w.deps })
      const sent = formatPreviousConversations(rows as never)

      expect(rows[1]).to.include({ content: '', _withheld: true })
      expect(rows[1]).to.not.have.any.keys('_ref', '_ver', '_pub')
      expect(sent[1]).to.include({ role: 'bot_response', content: '', _withheld: true })
      expect(sent[1]).to.not.have.any.keys('_ref', '_ver', '_pub')
    })

    it("Dana's next question is given her own earlier answer at its version and nothing of Bob's", async () => {
      const [qb, b] = bobsTurn()
      const qd = question('And what does R say?', DANA)
      const d = answer('R says revenue grew.', {}, { direct: { records: [R] } }, DANA)
      const w = bobsWorld([qb, b, qd, d])

      const sent = formatPreviousConversations((await includableRows(reader(DANA), session, [qb, b, qd, d], { deps: w.deps })) as never)

      expect(sealedAnswers(sent)).to.deep.equal([[String(d._id), d.labelVer]])
      expect(JSON.stringify(sent)).to.not.include("Bob's file")
    })

    it('a shared copy the sender may no longer see (removed) goes the same way', async () => {
      const copy = answer("Bob's file says the launch moved.", { messageType: 'shared_answer', authorUserId: BOB, removedAt: T2 }, { direct: { records: [BOBS_FILE] } }, BOB)
      const w = bobsWorld([copy])

      const [sent] = formatPreviousConversations((await includableRows(reader(DANA), session, [copy], { deps: w.deps })) as never)

      expect(sent).to.include({ role: 'shared_answer', content: '', _withheld: true })
      expect(sent).to.not.have.any.keys('_ref', '_ver', '_pub')
    })

    it("a row the history filter emptied is not sealed either, whatever the label check said", async () => {
      const a = answer('', { answerHidden: { redacted: true, reason: 'no_longer_accessible' } }, { pub: true })
      expect(sealedRef(a, true)).to.not.have.any.keys('_ref', '_ver', '_pub')
      const [sent] = formatPreviousConversations([a] as never)
      expect(sent).to.include({ content: '', _withheld: true })
      expect(sent).to.not.have.any.keys('_ref', '_ver', '_pub')
    })

    it("the chain: Dana reads both of her answers; Bob reads neither once the first used R; Carol reads both", async () => {
      const d1 = answer('R says revenue grew.', {}, { direct: { records: [R] } }, DANA)
      const d2 = answer('So the plan holds.', {}, { parents: [{ id: String(d1._id), ver: d1.labelVer, mode: 'text' }] }, DANA)
      const w = bobsWorld([d1, d2])
      for (const [who, expected] of [[DANA, 'visible'], [CAROL, 'visible'], [BOB, 'hidden']] as const) {
        const v = await verdictsFor(who, [d1, d2], w)
        expect([v.get(String(d1._id))?.verdict, v.get(String(d2._id))?.verdict], who).to.deep.equal([expected, expected])
      }
    })
  })

  describe('the public mark each sealed row carries (_pub)', () => {
    it('a complete answer that names nothing is public; one that names a record, and a question, are not', async () => {
      const q = question('Hi?')
      const open = answer('Hello.', {}, { pub: true })
      const named = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([q, open, named])
      const rows = await includableRows(reader(DANA), session, [q, open, named], { deps: w.deps })
      expect(rows[0]).to.include({ _ref: q._id, _pub: false })
      expect(rows[1]).to.include({ _ref: open._id, _pub: true })
      expect(rows[2]).to.include({ _ref: named._id, _pub: false })
    })

    it('an answer built only on public versions is public; one built on a restricted version is not', async () => {
      const base = answer('Hello.', {}, { pub: true })
      const restricted = answer('R says.', {}, { direct: { records: [R] } })
      const onOpen = answer('Building on hello.', {}, { parents: [{ id: base._id, ver: base.labelVer, mode: 'text' }] })
      const onR = answer('Building on R.', {}, { parents: [{ id: restricted._id, ver: restricted.labelVer, mode: 'text' }] })
      const w = world([base, restricted, onOpen, onR])
      const rows = await includableRows(reader(DANA), session, [onOpen, onR], { deps: w.deps })
      expect(rows[0]).to.include({ _ref: onOpen._id, _pub: true })
      expect(rows[1]).to.include({ _ref: onR._id, _pub: false })
    })

    it('a mark, a share by grant, a consent, a label that says it is not public, or no label: never public', async () => {
      const base = answer('Hello.', {}, { pub: true })
      const marked = answer('Fetched.', {}, { pub: true, direct: { marks: [{ source: 'searx', ver: 1 }] } })
      const granted = answer('Via a share.', {}, { parents: [{ id: base._id, ver: base.labelVer, mode: 'grant' }] })
      const consented = answer('Shared.', { consent: { by: DANA, at: T1, ver: 'v', scope: 'everyone' } }, { pub: true })
      const saysNo = answer('Plain.', {}, { pub: false })
      const unlabelled = answer('No label.', {}, null)
      const w = world([base, marked, granted, consented, saysNo, unlabelled])
      const rows = await includableRows(reader(DANA), session, [marked, granted, consented, saysNo, unlabelled], { deps: w.deps })
      for (const row of rows) {
        expect(row).to.have.property('_ref')
        expect(row).to.include({ _pub: false })
      }
    })

    it('with the label rule off the mark is still read from the stored labels, and no access is asked', async () => {
      const open = answer('Hello.', {}, { pub: true })
      const named = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([open, named])
      const rows = await includableRows(reader(DANA), session, [open, named], { deps: { ...w.deps, labelRule: async () => false } })
      expect(rows[0]).to.include({ _pub: true })
      expect(rows[1]).to.include({ _pub: false })
      expect(w.asked).to.deep.equal([])
    })

    it('a quote is marked by the version it quotes; a version not stored or a failed read is not public', async () => {
      const open = answer('Hello.', {}, { pub: true })
      const w = world([open])
      expect(await pubVersion(ORG, open, String(open.labelVer), { deps: w.deps })).to.equal(true)
      expect(await pubVersion(ORG, open, 'another-version', { deps: w.deps })).to.equal(false)
      const down: Partial<VisibilityDeps> = {
        ...w.deps,
        labels: async () => {
          throw new Error('store down')
        },
      }
      expect(await pubVersion(ORG, { _id: open._id, messageType: 'bot_response' }, String(open.labelVer), { deps: down })).to.equal(false)
    })
  })

  describe('an org whose answers are not judged by labels keeps the per-record view', () => {
    const off = (w: World): Partial<VisibilityDeps> => ({ ...w.deps, labelRule: async () => false })

    it('every row is shown, no access is asked and no label is read', async () => {
      const a = answer('Unlabelled.', {}, null)
      const w = world([a])
      w.deps.labels = async () => {
        throw new Error('no label is read')
      }
      const verdicts = await visibleRows(reader(BOB), session, [question('Q?'), a], { deps: off(w), member: true })
      expect(verdicts.get(String(a._id))).to.include({ verdict: 'visible', basis: 'before_labels' })
      expect(w.asked).to.deep.equal([])
    })

    it("every row goes through the per-record view, which decides what Bob sees", async () => {
      const q = question('What does R say?')
      const a = answer('R says.', {}, null)
      const w = world([q, a])
      const seen: unknown[][] = []
      const perRecordView = async <M extends object>(rows: M[]): Promise<M[]> => {
        seen.push(rows)
        return rows.map((m) => ((m as { messageType?: string }).messageType === 'bot_response' ? { ...m, answerHidden: { redacted: true, reason: 'no_access' } } : m))
      }
      const bob = await readerView(reader(BOB), session, [q, a], { deps: off(w), member: true, perRecordView, perRecordFor: () => false })
      expect(seen).to.have.length(1)
      expect(seen[0]).to.have.length(2)
      expect(bob.rows[1]).to.have.property('answerHidden')
      expect(bob.verdicts.get(String(a._id))).to.include({ verdict: 'hidden', reason: 'sources' })
      expect(bob.verdicts.get(String(q._id))).to.include({ verdict: 'visible' })
    })

    it("the sender's history goes as their own filter left it, sealed", async () => {
      const a = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([a])
      const rows = await includableRows(reader(BOB), session, [a], { deps: off(w) })
      expect(rows[0]).to.include({ content: 'R says.', _ref: a._id, _ver: a.labelVer })
      expect(w.asked).to.deep.equal([])
    })

    it('a rule that cannot be read is taken as on', async () => {
      const a = answer('R says.', {}, { direct: { records: [R] } })
      const w = world([a])
      const verdicts = await visibleRows(reader(BOB), session, [a], {
        deps: {
          ...w.deps,
          labelRule: async () => {
            throw new Error('flags down')
          },
        },
        member: true,
      })
      expect(verdicts.get(String(a._id))).to.include({ verdict: 'hidden' })
    })
  })
})

describe('the reader that stands for every member (`everyMemberOf`): shown only what every member is shown', () => {
  afterEach(() => resetVisibility())

  /** The room before Priya joined: each way into it is older than the rules. */
  const earlier: ReaderSession = { ...session, sharedWith: (session.sharedWith as unknown[]).slice(0, 3) }
  let seq = 100
  /** An answer the previous release stored: no label, the records it showed in its references. */
  const old = (content: string, records: string[] = []): ReaderRow & Record<string, unknown> => ({
    _id: nextId(),
    sessionId: ROOM,
    orgId: ORG,
    seq: ++seq,
    messageType: 'bot_response',
    content,
    referenceData: records.map((id) => ({ id, recordId: id, name: id })),
    createdAt: T0,
  })
  const names = (m: object): string[] =>
    ((m as { referenceData?: Array<{ recordId?: string }> }).referenceData ?? []).map((r) => r.recordId as string)
  /** The per-record view for whoever reads: an answer that names a record they cannot open is withheld whole. */
  const recordView = (asked: Readable[]) => async <M extends object>(list: M[], settled: Readable): Promise<M[]> => {
    asked.push(settled)
    return list.map((m) =>
      names(m).length > 0 ? ({ ...m, answerHidden: { redacted: true, reason: 'no_longer_accessible' } } as M) : m,
    )
  }
  const shown = async (rows: ReaderRow[], w: World, on: ReaderSession, asked: Readable[] = []): Promise<boolean[]> => {
    const { rows: out, verdicts } = await readerView(everyMemberOf(ORG), on, rows, { deps: w.deps, perRecordView: recordView(asked) })
    return rows.map(
      (r, i) =>
        verdicts.get(String(r._id))?.verdict === 'visible' && (out[i] as { answerHidden?: unknown }).answerHidden === undefined,
    )
  }

  it('an answer that used nothing; not one that used a record, an agent or a tool of its asker’s', async () => {
    const plain = answer('Nothing used.')
    const onR = answer('From R.', {}, { direct: { records: [R] } })
    const byAgent = answer('By the agent.', {}, { direct: { agents: [{ key: 'agent-x', kind: 'config' }] } })
    const personal = answer('From her tool.', {}, { direct: { personal: [DANA] } })
    const rows = [plain, onR, byAgent, personal]
    // Everyone in the room may use the agent, and Carol can open R: still not every member is known to.
    const w = world(rows, { agents: { 'agent-x': [DANA, CAROL, BOB, IVY, PRIYA] } })
    expect(await shown(rows, w, session)).to.deep.equal([true, false, false, false])
    expect((await verdictsFor(CAROL, [onR], w)).get(String(onR._id))?.verdict, 'Carol herself (control)').to.equal('visible')
    expect(w.asked.map((a) => a.userId), 'the access service is asked only for Carol').to.deep.equal([CAROL])
  })

  it('an answer from before the rules: only while each way into the room is older, and as the per-record view shows it', async () => {
    const plainOld = old('Old: nothing named.')
    const namedOld = old('Old: R says the launch moved.', [R])
    const rows = [plainOld, namedOld]
    const w = world(rows)
    expect(await shown(rows, w, session), 'Priya joined after the rules').to.deep.equal([false, false])
    const asked: Readable[] = []
    expect(await shown(rows, w, earlier, asked), 'everyone was in before').to.deep.equal([true, false])
    expect(asked.length).to.be.greaterThan(0)
    expect(
      asked.every((known) => known.askNoOne === true && (known.deadline ?? 0) > Date.now()),
      'the per-record view asks no one, and its store reads keep the read’s deadline',
    ).to.equal(true)
  })

  it('an answer built on one from before the rules, given as text: the same', async () => {
    const before = old('Old: nothing named.')
    const built = answer('So the plan holds.', {}, { parents: [{ id: String(before._id), ver: 'legacy', mode: 'text' }] }, CAROL)
    const rows = [before, built]
    const w = world(rows)
    Object.assign(w.deps, {
      rowsUpTo: async () => [before],
      chats: async () => new Map(),
      perRecordView: async (_reader: Reader, list: ReaderRow[], _owner: unknown, opts: { known?: Readable }) => {
        expect(opts.known?.askNoOne, 'asks no one').to.equal(true)
        return list
      },
    })
    const verdict = async (on: ReaderSession) =>
      (await visibleRows(everyMemberOf(ORG), on, [built], { deps: w.deps })).get(String(built._id))?.verdict
    expect(await verdict(session), 'Priya joined after the rules').to.not.equal('visible')
    expect(await verdict(earlier)).to.equal('visible')
    // As the file gate reads it (`readerView`): the earlier turn, which takes the store a moment, is read within the
    // read's own deadline, not cut short because the reader asks no one.
    Object.assign(w.deps, {
      rowsUpTo: async () => {
        await new Promise((resolve) => setTimeout(resolve, 20))
        return [before]
      },
    })
    const { verdicts } = await readerView(everyMemberOf(ORG), earlier, rows, { deps: w.deps, perRecordView: recordView([]) })
    expect(verdicts.get(String(built._id))?.verdict, 'as the gate reads it').to.equal('visible')
  })

  it('stores no verdict, where a member’s is stored', async () => {
    const a = answer('Nothing used.')
    const w = world([a], { epochs: { values: async (keys) => keys.map(() => '1') } })
    const puts: unknown[] = []
    w.deps.store = { getMany: async () => new Map(), putMany: async (entries: unknown[]) => void puts.push(...entries) } as never
    await visibleRows(everyMemberOf(ORG), session, [a], { deps: w.deps })
    expect(puts, 'every member').to.deep.equal([])
    await verdictsFor(CAROL, [a], w)
    expect(puts.length, 'Carol (control)').to.be.greaterThan(0)
  })
})

describe('where others read the chat, the asker’s own answers from before the rules reach her model only while every member could read them then', () => {
  afterEach(() => resetVisibility())

  /** The room before Priya joined: each way into it is older than the rules. */
  const earlier: ReaderSession = { ...session, sharedWith: (session.sharedWith as unknown[]).slice(0, 3) as never }
  const solo: ReaderSession = { _id: ROOM, orgId: ORG, userId: DANA, sharedWith: [] }
  /** Dana's turn from before the rules: her question and its answer, with no label, its steps stored. */
  const oldTurn = (content = 'Old: the launch moved.', answerOver: Record<string, unknown> = {}, questionOver: Record<string, unknown> = {}) => {
    const q = { ...question('When is the launch?'), createdAt: T0, ...questionOver }
    const a = answer(content, { createdAt: T0, parts: [{ type: 'tool_call', resultSummary: 'step' }, { type: 'text', content }], ...answerOver }, null)
    delete a.labelVer
    return [q, a] as const
  }
  const toDana = async (on: ReaderSession, rows: ReaderRow[], opts: Record<string, unknown> = {}) =>
    includableRows(reader(DANA), on, rows, { deps: world(rows).deps, ...opts })
  const field = (row: object | undefined, key: string): unknown => (row as Record<string, unknown> | undefined)?.[key]

  it('one every member could read then is given as text, sealed, so the new answer names it as a parent', async () => {
    const [q, a] = oldTurn()
    const [, old] = await toDana(earlier, [q, a])
    expect(old).to.include({ content: 'Old: the launch moved.', _ref: a._id, _ver: 'legacy' })
    expect(field(old, 'parts')).to.deep.equal([])
  })

  it('one that a member who joined after the rules could not read then is skipped: nothing of it goes, so it is no parent', async () => {
    const [q, a] = oldTurn()
    const [asked, old] = await toDana(session, [q, a])
    expect(old).to.include({ content: '' })
    expect(old).to.not.have.property('_ref')
    expect(old).to.not.have.property('_ver')
    expect(field(old, 'parts')).to.deep.equal([])
    expect(asked, 'her question still goes: every member reads it').to.include({ content: 'When is the launch?', _ref: q._id })
  })

  it('a member added later changes what the next run is given', async () => {
    const [q, a] = oldTurn()
    expect(field((await toDana(earlier, [q, a]))[1], 'content')).to.equal('Old: the launch moved.')
    expect(field((await toDana(session, [q, a]))[1], 'content')).to.equal('')
  })

  it('the asker alone again is given everything, its steps too', async () => {
    const [q, a] = oldTurn()
    const [, old] = await toDana(solo, [q, a])
    expect(old).to.include({ content: 'Old: the launch moved.', _ref: a._id })
    expect(field(old, 'parts')).to.have.length(2)
  })

  it('one whose turn names a source is skipped though everyone came before: no member is asked about it', async () => {
    const turns: Array<[string, readonly ReaderRow[]]> = [
      ['a record it showed', oldTurn('Old: R says.', { referenceData: [{ id: R, recordId: R, name: R }] })],
      ['a citation', oldTurn('Old: cited.', { citations: [{ citationData: { metadata: { recordId: R } } }] })],
      ['a collection its question picked', oldTurn('Old: from the board.', {}, { appliedFilters: { kb: [{ id: 'kb-1', name: 'Board' }] } })],
      ['a file its question attached', oldTurn('Old: from the file.', {}, { attachments: [{ recordId: 'up-1', recordName: 'f.txt' }] })],
    ]
    for (const [what, turn] of turns) {
      expect(field((await toDana(earlier, [...turn]))[1], 'content'), what).to.equal('')
      expect(field((await toDana(solo, [...turn]))[1], 'content'), `${what}, where only she reads`).to.not.equal('')
    }
  })

  it('the one her question replies to is given all the same, as she picked it', async () => {
    const [q, a] = oldTurn()
    const [, old] = await toDana(session, [q, a], { replyTargetId: String(a._id) })
    expect(old).to.include({ content: 'Old: the launch moved.', _ref: a._id, _ver: 'legacy' })
  })

  it('one whose question is not in the window is skipped: its turn cannot be judged', async () => {
    const [, a] = oldTurn()
    expect(field((await toDana(earlier, [a]))[0], 'content')).to.equal('')
  })

  it('a chat visible to its project: given while it was so before the rules, skipped once it became so after them', async () => {
    const project: ReaderSession = { ...session, sharedWith: [], projectId: '7a0000000000000000000007', projectVisibility: 'project' }
    const [q, a] = oldTurn()
    expect(field((await toDana(project, [q, a]))[1], 'content')).to.equal('Old: the launch moved.')
    expect(field((await toDana({ ...project, projectVisibleAt: T2 }, [q, a]))[1], 'content')).to.equal('')
  })

  it('rows read with no id are judged the same: given while every member came before, skipped once one joined after', async () => {
    const [q, a] = oldTurn()
    delete q._id
    delete a._id
    expect(field((await toDana(earlier, [q, a]))[1], 'content')).to.equal('Old: the launch moved.')
    expect(field((await toDana(session, [q, a]))[1], 'content')).to.equal('')
  })

  it('a card from before the rules goes with its turn’s answer, and is skipped with it', async () => {
    const [q, a] = oldTurn()
    const card = { _id: nextId(), sessionId: ROOM, orgId: ORG, messageType: 'tool_call', requestedBy: DANA, content: 'Which launch: web or mobile?', createdAt: T0 }
    const rows = [q, card, a]
    const before = await toDana(earlier, rows)
    expect(field(before[1], 'content'), 'given with its answer').to.equal('Which launch: web or mobile?')
    expect(field(before[2], 'content')).to.equal('Old: the launch moved.')
    const after = await toDana(session, rows)
    expect(field(after[1], 'content'), 'skipped with its answer').to.equal('')
    expect(field(after[2], 'content')).to.equal('')
    expect(field((await toDana(solo, rows))[1], 'content'), 'hers alone').to.equal('Which launch: web or mobile?')
  })

  it('another person’s answer from before the rules takes the same rule: given while every member could read it then, skipped once one joined after, given when the question replies to it', async () => {
    // Dana's old turn, in Carol's run: Carol could read the room then.
    const [q, a] = oldTurn()
    const toCarol = async (on: ReaderSession, opts: Record<string, unknown> = {}) =>
      (await includableRows(reader(CAROL), on, [q, a], { deps: world([q, a]).deps, ...opts }))[1]
    expect(await toCarol(earlier)).to.include({ content: 'Old: the launch moved.', _ref: a._id, _ver: 'legacy' })
    const skipped = await toCarol(session)
    expect(skipped).to.include({ content: '' })
    expect(skipped).to.not.have.property('_ref')
    expect(skipped).to.not.have.property('_ver')
    expect(await toCarol(session, { replyTargetId: String(a._id) })).to.include({ content: 'Old: the launch moved.', _ref: a._id })
  })
})
