import { expect } from 'chai'
import {
  resetVisibility,
  visibleRows,
  includableRows,
  widenOK,
  type Reader,
  type ReaderRow,
  type ReaderSession,
  type VisibilityDeps,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { VerdictStore } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/verdict-store'
import { labelContentHash } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label'

/**
 * A long chat: each answer is given the answers before it (a window of them), as the ledger names the history the
 * model was given. Whatever the chat's length, a reader who can open everything sees every answer, and the read never
 * walks the chain a level at a time.
 */

const ORG = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const ROOM = 'bbbbbbbbbbbbbbbbbbbbbbbb'
const DANA = 'd00000000000000000000001'
const CAROL = 'c00000000000000000000002'
const BOB = 'b00000000000000000000003'

const T1 = new Date('2026-10-02T10:00:00Z')

const session: ReaderSession = {
  _id: ROOM,
  orgId: ORG,
  userId: DANA,
  sharedWith: [
    { userId: CAROL, accessLevel: 'write', addedAt: T1 },
    { userId: BOB, accessLevel: 'write', addedAt: T1 },
  ],
}

const reader = (userId: string): Reader => ({ userId, orgId: ORG })

let n = 0
const nextId = (): string => `${(++n).toString(16).padStart(24, '0')}`

const DIRECT_KEYS = ['records', 'vrids', 'nodes', 'saVrids', 'personal', 'agents', 'chat', 'projects', 'marks']

const answer = (
  content: string,
  records: string[],
  parents: Array<{ id: string; ver: string; mode: string }>,
  requestedBy: string,
): ReaderRow & Record<string, unknown> => {
  const labelVer = nextId()
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
    createdAt: T1,
    provenance: {
      v: 5,
      ver: labelVer,
      contentHash: labelContentHash(content, parts),
      direct: { ...Object.fromEntries(DIRECT_KEYS.map((k) => [k, []])), records },
      parents,
      complete: true,
      tagset: 1,
      pub: false,
    },
  }
}

/** `turns` answers asked in turn by the askers; each names its record and, as parents, the `window` answers before it. */
const chain = (turns: number, window: number, askers: string[], mode = 'text'): Array<ReaderRow & Record<string, unknown>> => {
  const rows: Array<ReaderRow & Record<string, unknown>> = []
  for (let t = 0; t < turns; t += 1) {
    const parents = rows.slice(-window).map((r) => ({ id: String(r._id), ver: String(r.labelVer), mode }))
    rows.push(answer(`Answer ${t}.`, [`rec-${t % 50}`], parents, askers[t % askers.length] as string))
  }
  return rows
}

interface Counts {
  labelReads: number
  accessRequests: number
}

const world = (rows: ReaderRow[], opens: Record<string, (id: string) => boolean>): { deps: Partial<VisibilityDeps>; counts: Counts } => {
  const store = new Map(rows.map((r) => [String(r._id), r]))
  const counts: Counts = { labelReads: 0, accessRequests: 0 }
  const deps: Partial<VisibilityDeps> = {
    readable: async (who, ids) => {
      counts.accessRequests += 1
      const can = opens[who.userId] ?? (() => false)
      return { readable: new Set(ids.filter(can)), asked: new Set(ids), deleted: new Set(), failed: false, validUntil: Date.now() + 30_000 }
    },
    labels: async (_org, ids) => {
      counts.labelReads += 1
      return new Map(ids.flatMap((id) => (store.has(id) ? [[id, store.get(id) as ReaderRow]] : [])))
    },
    runs: async () => [],
    canUseAgent: async () => undefined,
    canReadConversation: async () => undefined,
    canReadProject: async () => undefined,
    isServiceAgent: async () => false,
    sharedText: (text) => text,
    switchAt: async () => T1.getTime(),
    labelRule: async () => true,
    store: new VerdictStore(),
  }
  return { deps, counts }
}

const everything = (): boolean => true

describe('the reader check in a long chat', () => {
  afterEach(() => {
    resetVisibility()
  })

  it('an answer built on 2,000 answers before it is shown to its asker and to every reader who can open them all', async () => {
    const rows = chain(2_000, 50, [DANA, CAROL])
    const { deps } = world(rows, { [DANA]: everything, [CAROL]: everything, [BOB]: (id) => id !== 'rec-7' })
    const page = rows.slice(-10)
    for (const who of [DANA, CAROL]) {
      const verdicts = await visibleRows(reader(who), session, page, { deps, member: true })
      expect(page.map((r) => verdicts.get(String(r._id))?.verdict), who).to.deep.equal(page.map(() => 'visible'))
    }
    // Bob cannot open a record of turn 7: every answer built on it, however far back, is hidden from him.
    const bob = await visibleRows(reader(BOB), session, page, { deps, member: true })
    expect(page.map((r) => bob.get(String(r._id))?.verdict)).to.deep.equal(page.map(() => 'hidden'))
  })

  it('a solo chat of 2,000 answers, each given its own earlier ones whole, stays its asker\'s to read', async () => {
    const rows = chain(2_000, 50, [DANA], 'full')
    const { deps } = world(rows, { [DANA]: everything })
    const page = rows.slice(-10)
    const verdicts = await visibleRows(reader(DANA), { _id: ROOM, orgId: ORG, userId: DANA }, page, { deps, member: true })
    expect(page.map((r) => verdicts.get(String(r._id))?.verdict)).to.deep.equal(page.map(() => 'visible'))
  })

  it('one access request covers the whole chain behind a page', async () => {
    const rows = chain(300, 50, [DANA, CAROL])
    const { deps, counts } = world(rows, { [CAROL]: everything })
    await visibleRows(reader(CAROL), session, rows.slice(-10), { deps, member: true })
    expect(counts.accessRequests).to.equal(1)
  })

  it('an answer at the end of a 2,000-answer chat can be shared: the share check reads closures, not 32 levels of the chain', async () => {
    const rows = chain(2_000, 50, [DANA])
    const { deps } = world(rows, { [DANA]: everything })
    const top = rows[rows.length - 1] as ReaderRow & Record<string, unknown>
    expect(await widenOK(top, String(top.labelVer), 'everyone', { deps, session })).to.deep.equal({ ok: true })
    // One record of turn 7 Dana can no longer open: nothing built on it may be shared.
    const { deps: lost } = world(rows, { [DANA]: (id) => id !== 'rec-7' })
    expect(await widenOK(top, String(top.labelVer), 'everyone', { deps: lost, session })).to.deep.equal({ ok: false, code: 'SHARE_NOT_WIDENABLE' })
  })

  it("the sender's model is given her own answers at the end of a 2,000-answer chat", async () => {
    const rows = chain(2_000, 50, [DANA])
    const { deps } = world(rows, { [DANA]: everything })
    const window = rows.slice(-20)
    const given = await includableRows(reader(DANA), session, window, { deps })
    expect(given.map((r) => (r as { content?: string }).content)).to.deep.equal(window.map((r) => r.content))
  })
})
