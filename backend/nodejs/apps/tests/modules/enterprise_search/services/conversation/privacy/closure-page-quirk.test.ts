import { expect } from 'chai'
import { readFileSync } from 'fs'
import { join } from 'path'
import {
  resetVisibility,
  visibleRows,
  type ReaderSession,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { depsOf, type Row, type World } from './closure-world'

/**
 * Three chats the closure property test found (its seeds 49, 165 and 277, as its first generator made them): a page
 * holds an answer whose stored text no longer matches its label, and another answer built on it. A row's verdict no
 * longer depends on the page it is read on: it is the same as when it is read alone.
 */

interface Case {
  seed: number
  reader: string
  sessionId: string | null
  member: boolean | null
  rowId: string
  page: string[]
  closures: { verdict: string; reason?: string; basis?: string }
  world: {
    rows: Row[]
    sessions: ReaderSession[]
    opens: Record<string, string[]>
    deleted: string[]
    agents: Record<string, string[] | null>
    chatsRead: Record<string, Record<string, boolean | null>>
    projectsRead: Record<string, Record<string, boolean | null>>
    failing: string[]
    switchAt: number | 'fails' | null
  }
}

const cases = JSON.parse(readFileSync(join(__dirname, 'fixtures', 'closure-page-quirk.json'), 'utf8')) as Case[]

const worldOf = (c: Case): World => {
  const maps = (o: Record<string, Record<string, boolean | null>>) =>
    new Map(Object.entries(o).map(([k, v]) => [k, new Map(Object.entries(v).map(([c2, x]) => [c2, x ?? undefined]))]))
  return {
    rows: c.world.rows,
    store: new Map(c.world.rows.map((r) => [String(r._id), r])),
    sessions: new Map(c.world.sessions.map((s) => [String(s._id), s])),
    opens: new Map(Object.entries(c.world.opens).map(([k, v]) => [k, new Set(v)])),
    deleted: new Set(c.world.deleted),
    agents: new Map(Object.entries(c.world.agents).map(([k, v]) => [k, v === null ? undefined : new Set(v)])),
    chatsRead: maps(c.world.chatsRead),
    projectsRead: maps(c.world.projectsRead),
    failing: new Set(c.world.failing),
    switchAt: c.world.switchAt ?? undefined,
    timeline: [],
  }
}

describe('a row is judged the same on every page (closure property seeds 49, 165, 277)', () => {
  afterEach(() => {
    resetVisibility()
  })

  for (const c of cases) {
    it(`seed ${String(c.seed)}: ${c.closures.verdict}${c.closures.reason ? ` (${c.closures.reason})` : ''} on its page and alone`, async () => {
      const world = worldOf(c)
      const deps = depsOf(world)
      const session = c.sessionId === null ? undefined : world.sessions.get(c.sessionId)
      const reader = { userId: c.reader, orgId: String(world.rows[0]?.orgId) }
      const opts = { deps, ...(c.member !== null && { member: c.member }) }
      const page = c.page.map((id) => world.store.get(id) as Row)
      const row = world.store.get(c.rowId) as Row
      const onPage = (await visibleRows(reader, session, page, opts)).get(c.rowId)
      const alone = (await visibleRows(reader, session, [row], opts)).get(c.rowId)
      const shape = (v: typeof onPage) => ({ verdict: v?.verdict, ...(v?.reason && { reason: v.reason }), ...(v?.basis && { basis: v.basis }) })
      expect(shape(onPage)).to.deep.equal(c.closures)
      expect(shape(alone)).to.deep.equal(c.closures)
    })
  }
})
