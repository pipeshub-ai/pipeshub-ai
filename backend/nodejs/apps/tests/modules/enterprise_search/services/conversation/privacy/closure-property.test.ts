import { expect } from 'chai'
import { labelStateOf } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label'
import * as closures from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import * as walk from './reference/visibility-walk'
import { BOB, CAROL, IVY, ORG, OTHER, PEOPLE, Rng, depsOf, makeWorld, readersOf, type Row, type World } from './closure-world'

/** A chat that is none of the world's: read as a fork of one reads (`chatsRead`), where no share of them covers anyone. */
const FORK = 'cccccccccccccccccccccccc'

/**
 * The reader check judges by closures; the walk it replaced is kept as the reference. For random chats and readers,
 * both must give every row the same verdict, reason and basis, the same answer to "may this reader read this version
 * whole", the same public mark, and the same history for the sender's model.
 */

const WORLDS = Number(process.env.CLOSURE_PROPERTY_WORLDS ?? 400)
const FIRST_SEED = Number(process.env.CLOSURE_PROPERTY_SEED ?? 1)

type Verdicts = Map<string, { verdict: string; reason?: string; basis?: string }>

const shape = (verdicts: Map<string, { verdict: string; reason?: string; basis?: string }>): Verdicts =>
  new Map([...verdicts].map(([id, v]) => [id, { verdict: v.verdict, ...(v.reason !== undefined && { reason: v.reason }), ...(v.basis !== undefined && { basis: v.basis }) }]))

/**
 * The version a row is asked about whole and marked by: its current one. Every caller asks that one; the walk read only
 * the tree behind a row's current version, so an archived version asked on its own was judged without its sources.
 */
const currentOf = (row: Row): string => (typeof row.labelVer === 'string' ? row.labelVer : 'legacy')

const pageOf = (world: World, rng: Rng, sessionId: string | undefined): Row[] => {
  const inChat = world.rows.filter((r) => sessionId === undefined || r.sessionId === sessionId || rng.chance(0.1))
  if (inChat.length === 0) return world.rows.slice(-3)
  const start = rng.int(inChat.length)
  return inChat.slice(start, start + 1 + rng.int(8))
}

/**
 * The versions a row's label at `ver` names, the versions that wrote the files it names, and what its share, or a copy,
 * counts on or was made from, by `id@ver`.
 */
const namedBy = (world: World, row: Row, ver: string): string[] => {
  const current = typeof row.labelVer === 'string' ? row.labelVer : 'legacy'
  const entry = Array.isArray(row.labelHistory) ? (row.labelHistory as Array<Record<string, unknown>>).find((e) => e.ver === ver) : undefined
  const counted = (on: unknown): string[] =>
    (Array.isArray(on) ? (on as Array<Record<string, unknown>>) : []).map((ref) => `${String(ref.row)}@${String(ref.ver)}`)
  if (row.messageType === 'shared_answer') {
    const from = (row.removedAt === undefined ? row.sharedFrom : row.removedFrom) as Record<'messageId' | 'ver', string> | undefined
    const again = row.again as string | undefined
    return [
      ...counted((row.sharedFrom as Record<string, unknown> | undefined)?.on),
      ...(from?.messageId !== undefined ? [`${from.messageId}@${from.ver}`] : []),
      ...(again !== undefined ? [`${again}@legacy`] : []),
    ]
  }
  const consent = (ver === current ? row.consent : entry?.consent) as Record<string, unknown> | undefined
  const label = (ver === current ? row.provenance : entry?.provenance) as { parents?: unknown } | undefined
  const out: string[] = counted(consent?.on)
  for (const parent of Array.isArray(label?.parents) ? (label?.parents as Array<Record<string, unknown>>) : []) {
    if (typeof parent !== 'object' || parent === null) continue
    if (typeof parent.id === 'string' && parent.id !== '') {
      out.push(`${parent.id}@${String(parent.ver)}`)
      continue
    }
    for (const w of world.rows) {
      if (w.runId === parent.runId && typeof w.labelVer === 'string') out.push(`${String(w._id)}@${w.labelVer}`)
      for (const e of Array.isArray(w.labelHistory) ? (w.labelHistory as Array<Record<string, unknown>>) : []) {
        if (e.runId === parent.runId) out.push(`${String(w._id)}@${String(e.ver)}`)
      }
    }
  }
  return out
}

/**
 * The rows of a page the walk let the page decide: it read each page row once, as its own, so a page row whose text no
 * longer matches its label (no write path makes one) had its label skipped where another row of the page was built
 * on it, and that row was judged without its sources. Closures judge each row the same on every page.
 */
const decidedByPage = (world: World, page: Row[]): Set<string> => {
  const stale = new Set(page.filter((r) => labelStateOf(r) === 'stale').map((r) => `${String(r._id)}@${String(r.labelVer)}`))
  const out = new Set<string>()
  if (stale.size === 0) return out
  for (const row of page) {
    const seen = new Set<string>()
    const todo = namedBy(world, row, typeof row.labelVer === 'string' ? row.labelVer : 'legacy')
    while (todo.length > 0) {
      const key = todo.pop() as string
      if (seen.has(key)) continue
      seen.add(key)
      if (stale.has(key)) {
        out.add(String(row._id))
        break
      }
      const [id, ver] = key.split('@') as [string, string]
      const below = world.store.get(id)
      if (below) todo.push(...namedBy(world, below, ver))
    }
  }
  // A card follows its turn's answer.
  if (out.size > 0) for (const row of page) if (row.messageType === 'tool_call') out.add(String(row._id))
  return out
}

/** The parts of a history row the sender's model and the outbound check read. */
const sent = (row: object): Record<string, unknown> => {
  const r = row as Record<string, unknown>
  return { id: String(r._id), content: r.content, ref: r._ref, ver: r._ver, pub: r._pub, withheld: r._withheld }
}

describe('closures give the verdicts of the walk they replace', function () {
  this.timeout(600_000)

  afterEach(() => {
    closures.resetVisibility()
    walk.resetVisibility()
  })

  it(`over ${String(WORLDS)} random chats, for every reader: verdicts, whole reads, public marks and model history`, async () => {
    const differences: string[] = []
    let judged = 0
    let pageDecided = 0
    // What the worlds reach, so no rule is compared only where it never applies.
    const met = { on: 0, limited: 0, agent: 0, signIn: 0, plain: 0, covered: 0, askedMore: 0, unsaidOpen: 0, unsaidClosed: 0 }
    for (let seed = FIRST_SEED; seed < FIRST_SEED + WORLDS && differences.length < 5; seed += 1) {
      const world = makeWorld(seed)
      const deps = depsOf(world)
      const { hasClosedSkill } = deps
      deps.hasClosedSkill = async (who, key) => {
        const closed = await hasClosedSkill?.(who, key)
        if (closed === false) met.unsaidOpen += 1
        else met.unsaidClosed += 1
        return closed
      }
      const rng = new Rng(seed * 7919)
      // Drawn apart, so the readers and pages of each seed stay what they were.
      const asChat = new Rng(seed * 104729)
      for (const { reader, session, member } of readersOf(world, rng)) {
        const sessionId = session?._id as string | undefined
        // Some reads happen as another chat's (`chatsRead`): a fork deciding what to copy, or a read of the other chat.
        const chatsRead = asChat.chance(0.2) ? [FORK] : asChat.chance(0.1) ? [OTHER] : undefined
        const as = chatsRead ? ` as ${chatsRead[0] ?? ''}` : ''
        for (let p = 0; p < 2; p += 1) {
          const page = pageOf(world, rng, sessionId)
          const opts = { deps, ...(member !== undefined && { member }), ...(chatsRead && { chatsRead }) }
          const mine = shape(await closures.visibleRows(reader, session, page, opts))
          const theirs = shape(await walk.visibleRows(reader, session, page, opts))
          judged += page.length
          const skip = decidedByPage(world, page)
          pageDecided += skip.size
          for (const row of page) {
            const id = String(row._id)
            if (skip.has(id)) continue
            const a = JSON.stringify(mine.get(id))
            const b = JSON.stringify(theirs.get(id))
            if (a !== b) differences.push(`seed ${String(seed)} reader ${reader.userId} chat ${String(sessionId)}${as} row ${id} (${String(row.messageType)}): closures ${a}, walk ${b}`)
          }
          const sender = { ...reader }
          const sendOpts = { deps, ...(chatsRead && { chatsRead }) }
          const mineSent = (await closures.includableRows(sender, session, page, sendOpts)).map(sent).filter((r) => !skip.has(r.id as string))
          const theirsSent = (await walk.includableRows(sender, session, page, sendOpts)).map(sent).filter((r) => !skip.has(r.id as string))
          if (JSON.stringify(mineSent) !== JSON.stringify(theirsSent)) {
            differences.push(`seed ${String(seed)} sender ${reader.userId} chat ${String(sessionId)}${as}: history ${JSON.stringify(mineSent)} vs ${JSON.stringify(theirsSent)}`)
          }
        }
        for (const row of world.rows) {
          if (row.messageType !== 'bot_response' && row.messageType !== 'error') continue
          const ver = currentOf(row)
          const fullOpts = { deps, ...(session !== undefined && { session }) }
          const a = await closures.fullOK(reader, row, ver, fullOpts)
          const b = await walk.fullOK(reader, row, ver, fullOpts)
          if (a !== b) differences.push(`seed ${String(seed)} reader ${reader.userId} fullOK ${String(row._id)}@${ver}: closures ${String(a)}, walk ${String(b)}`)
        }
      }
      for (const row of world.rows) {
        const ver = currentOf(row)
        const a = await closures.pubVersion(ORG, row, ver, { deps })
        const b = await walk.pubVersion(ORG, row, ver, { deps })
        if (a !== b) differences.push(`seed ${String(seed)} pubVersion ${String(row._id)}@${ver}: closures ${String(a)}, walk ${String(b)}`)
      }
      for (const row of world.rows) {
        if (row.messageType !== 'bot_response' && row.messageType !== 'error') continue
        const session = world.shareSessions.get(String(row.sessionId))
        for (const audience of ['everyone', 'outside', { outside: [CAROL, BOB, IVY] }, { userIds: [CAROL, BOB, IVY] }] as const) {
          const opts = { deps, ...(session !== undefined && { session }) }
          const widened = await closures.widenOK(row, currentOf(row), audience, opts)
          const a = JSON.stringify(widened)
          const b = JSON.stringify(await walk.widenOK(row, currentOf(row), audience, opts))
          if (a !== b) differences.push(`seed ${String(seed)} widenOK ${String(row._id)} to ${JSON.stringify(audience)}: closures ${a}, walk ${b}`)
          if (widened.on) met.on += 1
          if (widened.limitedTo) met.limited += 1
          if (widened.ok && !widened.on && !widened.limitedTo) met.plain += 1
          if (widened.code === 'SHARE_NOT_ALLOWED_AGENT') met.agent += 1
          if (widened.code === 'SHARE_NOT_ALLOWED_SIGN_IN') met.signIn += 1
        }
        // Whether a share covers a person, asked of the row alone.
        if (row.consent !== undefined && session !== undefined) {
          const viewer = { userId: rng.pick(PEOPLE), orgId: ORG, member: true, memberSince: closures.memberSinceOf(session, rng.pick(PEOPLE)) }
          const a = await closures.shareCovers(viewer, row, currentOf(row), { session, deps })
          const b = await walk.shareCovers(viewer, row, currentOf(row), { session, deps })
          if (a !== b) differences.push(`seed ${String(seed)} shareCovers ${String(row._id)} for ${viewer.userId}: closures ${String(a)}, walk ${String(b)}`)
          if (a) met.covered += 1
          else if (closures.consentCovers(row, currentOf(row), viewer)) met.askedMore += 1
        }
      }
    }
    expect(differences, differences.join('\n')).to.deep.equal([])
    expect(judged).to.be.greaterThan(WORLDS * 4)
    // Far from every row: the case stays rare.
    expect(pageDecided).to.be.lessThan(judged / 20)
    for (const [what, times] of Object.entries(met)) expect(times, `${what}: ${JSON.stringify(met)}`).to.be.greaterThan(WORLDS >= 400 ? 15 : -1)
  })
})
