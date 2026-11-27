import { expect } from 'chai'
import { labelStateOf } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label'
import * as closures from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import * as walk from './reference/visibility-walk'
import { BOB, CAROL, IVY, ORG, OTHER, PEOPLE, ROOM, Rng, depsOf, makeWorld, memoryClosureStore, readersOf, type Row, type World } from './closure-world'

/** A chat that is none of the world's: read as a fork of one reads (`chatsRead`), where no share of them covers anyone. */
const FORK = 'cccccccccccccccccccccccc'

/**
 * Closures as production keeps them: each version's closure is made when it is written, from the stored closures of
 * the versions it was given, and kept; shares come and go after; reads take the kept closures, notice the shares made
 * since and make those closures again. For random chats replayed in the order they were written, the reads must give
 * what the walk gives on the chat as it ends.
 */

const WORLDS = Number(process.env.CLOSURE_STORE_WORLDS ?? 300)
const FIRST_SEED = Number(process.env.CLOSURE_STORE_SEED ?? 1)

const shape = (v: { verdict: string; reason?: string; basis?: string } | undefined) =>
  v && { verdict: v.verdict, ...(v.reason !== undefined && { reason: v.reason }), ...(v.basis !== undefined && { basis: v.basis }) }

const pageOf = (world: World, rng: Rng, sessionId: string | undefined): Row[] => {
  const inChat = world.rows.filter((r) => sessionId === undefined || r.sessionId === sessionId || rng.chance(0.1))
  if (inChat.length === 0) return world.rows.slice(-3)
  const start = rng.int(inChat.length)
  return inChat.slice(start, start + 1 + rng.int(8))
}

/** Rows the walk let the page decide (see the property test): left out of the comparison. */
const decidedByPage = (store: ReadonlyMap<string, Row>, page: Row[]): Set<string> => {
  const stale = new Set(page.filter((r) => labelStateOf(r) === 'stale').map((r) => String(r._id)))
  const out = new Set<string>()
  if (stale.size === 0) return out
  // Any page row built on anything: a stale row on the page is rare enough to leave the page out whole.
  for (const row of page) if (!stale.has(String(row._id))) out.add(String(row._id))
  return out
}

describe('kept closures give the verdicts of the walk, with shares made after them', function () {
  this.timeout(900_000)

  afterEach(() => {
    closures.resetVisibility()
    walk.resetVisibility()
  })

  it(`over ${String(WORLDS)} random chats replayed in order`, async () => {
    const differences: string[] = []
    let judged = 0
    let kept = 0
    let remade = 0
    for (let seed = FIRST_SEED; seed < FIRST_SEED + WORLDS && differences.length < 5; seed += 1) {
      closures.resetVisibility()
      const world = makeWorld(seed)
      const current = new Map<string, Row>()
      const replay: World = { ...world, store: current }
      const deps = depsOf(replay)
      const store = memoryClosureStore(current)
      closures.configureVisibility({ ...deps, closures: store })
      const rng = new Rng(seed * 104729)
      for (const event of world.timeline) {
        current.set(String(event.row._id), structuredClone(event.row))
        if (event.kind === 'write') await closures.rememberAnswerClosures(ORG, [current.get(String(event.row._id)) as Row])
        if (rng.chance(0.15)) {
          // A read in between: what it makes again is kept for the reads after.
          const rows = [...current.values()].slice(-1 - rng.int(5))
          await closures.visibleRows({ userId: rng.pick(PEOPLE), orgId: ORG }, world.sessions.get(ROOM), rows, { member: true })
        }
      }
      const before = store.counts.kept
      // Drawn apart, so the readers and pages of each seed stay what they were.
      const asChat = new Rng(seed * 15485863)
      for (const { reader, session, member } of readersOf(world, rng)) {
        const sessionId = session?._id as string | undefined
        const page = pageOf(world, rng, sessionId).map((r) => current.get(String(r._id)) as Row)
        // Some reads happen as another chat's (`chatsRead`): a fork deciding what to copy, or a read of the other chat.
        const chatsRead = asChat.chance(0.2) ? [FORK] : asChat.chance(0.1) ? [OTHER] : undefined
        const as = chatsRead ? ` as ${chatsRead[0] ?? ''}` : ''
        const opts = { ...(member !== undefined && { member }), ...(chatsRead && { chatsRead }) }
        const mine = await closures.visibleRows(reader, session, page, opts)
        const theirs = await walk.visibleRows(reader, session, page, { ...opts, deps })
        const skip = decidedByPage(current, page)
        judged += page.length
        for (const row of page) {
          const id = String(row._id)
          if (skip.has(id)) continue
          const a = JSON.stringify(shape(mine.get(id)))
          const b = JSON.stringify(shape(theirs.get(id)))
          if (a !== b) differences.push(`seed ${String(seed)} reader ${reader.userId} chat ${String(sessionId)}${as} row ${id} (${String(row.messageType)}): kept closures ${a}, walk ${b}`)
        }
        for (const row of page) {
          if (row.messageType !== 'bot_response' && row.messageType !== 'error') continue
          const ver = typeof row.labelVer === 'string' ? row.labelVer : 'legacy'
          const a = await closures.fullOK(reader, row, ver, { ...(session !== undefined && { session }) })
          const b = await walk.fullOK(reader, row, ver, { deps, ...(session !== undefined && { session }) })
          if (a !== b) differences.push(`seed ${String(seed)} reader ${reader.userId} fullOK ${String(row._id)}: kept closures ${String(a)}, walk ${String(b)}`)
          const p = await closures.pubVersion(ORG, row, ver)
          const q = await walk.pubVersion(ORG, row, ver, { deps })
          if (p !== q) differences.push(`seed ${String(seed)} pubVersion ${String(row._id)}: kept closures ${String(p)}, walk ${String(q)}`)
        }
      }
      for (const row of current.values()) {
        if (row.messageType !== 'bot_response' && row.messageType !== 'error') continue
        const session = world.shareSessions.get(String(row.sessionId))
        const ver = typeof row.labelVer === 'string' ? row.labelVer : 'legacy'
        const audiences: closures.WidenAudience[] = ['everyone', 'outside', { outside: [CAROL, BOB, IVY] }, { userIds: [CAROL, BOB, IVY] }]
        for (const audience of audiences) {
          const a = JSON.stringify(await closures.widenOK(row, ver, audience, { ...(session !== undefined && { session }) }))
          const b = JSON.stringify(await walk.widenOK(row, ver, audience, { deps, ...(session !== undefined && { session }) }))
          if (a !== b) differences.push(`seed ${String(seed)} widenOK ${String(row._id)} to ${JSON.stringify(audience)}: kept closures ${a}, walk ${b}`)
        }
      }
      kept += store.counts.kept
      remade += store.counts.kept - before
    }
    expect(differences, differences.join('\n')).to.deep.equal([])
    expect(judged).to.be.greaterThan(WORLDS * 4)
    // The kept closures are used, and shares made after them are noticed.
    expect(kept).to.be.greaterThan(WORLDS)
    expect(remade).to.be.greaterThan(0)
  })
})
