import { expect } from 'chai'
import * as visibility from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import type { Reader, ReaderRow, VisibilityDeps } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { ORG, OTHER, PEOPLE, Rng, depsOf, makeWorld, memoryClosureStore, type Row } from './closure-world'

/** A chat that is none of the world's: read as a fork of one reads (`chatsRead`), where no share of them covers anyone. */
const FORK = 'cccccccccccccccccccccccc'

const WORLDS = Number(process.env.VISIBLE_TO_EACH_WORLDS ?? 200)
const FIRST_SEED = Number(process.env.VISIBLE_TO_EACH_SEED ?? 1)

const shape = (v: visibility.RowVerdict | undefined) =>
  v && { verdict: v.verdict, ...(v.reason !== undefined && { reason: v.reason }), ...(v.basis !== undefined && { basis: v.basis }) }

const isAnswer = (row: Row): boolean => row.messageType === 'bot_response' || row.messageType === 'error'

/**
 * A notice decides who it tells with `visibleToEach`. For random chats, each person's verdict of each answer, read in
 * the answer's own chat as a notice reads it, or as a fork or another chat reads it (`chatsRead`), a member or not, and
 * with closures kept or not, must be the one `visibleRows` gives that person alone.
 */
describe('visibleToEach gives each reader the verdict visibleRows gives them alone', function () {
  this.timeout(600_000)

  afterEach(() => visibility.resetVisibility())

  it(`over ${String(WORLDS)} random chats, every answer, every person`, async () => {
    const differences: string[] = []
    let compared = 0
    // What the comparisons covered: a property that only ever met one kind of verdict would prove little.
    const seen = new Map<string, number>()
    for (let seed = FIRST_SEED; seed < FIRST_SEED + WORLDS && differences.length < 5; seed += 1) {
      const world = makeWorld(seed)
      const rng = new Rng(seed * 6271)
      const readers: Reader[] = PEOPLE.map((userId) => ({ userId, orgId: ORG }))
      for (const kept of [false, true]) {
        for (const row of world.rows.filter(isAnswer)) {
          const session = world.sessions.get(String(row.sessionId))
          const chatsRead = rng.chance(0.2) ? [FORK] : rng.chance(0.1) ? [OTHER] : undefined
          const member = rng.chance(0.8) ? true : rng.chance(0.5) ? undefined : false
          const optsWith = (deps: Partial<VisibilityDeps>) => ({
            deps,
            labelsLoaded: true,
            ...(member !== undefined && { member }),
            ...(chatsRead && { chatsRead }),
          })
          // Each side its own closures, kept or made, so neither reads what the other kept.
          const depsFor = (): Partial<VisibilityDeps> => ({ ...depsOf(world), ...(kept && { closures: memoryClosureStore(world.store) }) })
          visibility.resetVisibility()
          const alone = new Map<string, ReturnType<typeof shape>>()
          const aloneDeps = depsFor()
          for (const reader of readers) {
            alone.set(reader.userId, shape((await visibility.visibleRows(reader, session, [row], optsWith(aloneDeps))).get(String(row._id))))
          }
          visibility.resetVisibility()
          const each = await visibility.visibleToEach(readers, session, row as ReaderRow, optsWith(depsFor()))
          readers.forEach((reader, i) => {
            compared += 1
            const kind = each[i]?.verdict ?? 'none'
            seen.set(kind, (seen.get(kind) ?? 0) + 1)
            const a = JSON.stringify(shape(each[i]))
            const b = JSON.stringify(alone.get(reader.userId))
            if (a !== b) {
              const as = chatsRead ? ` as ${chatsRead[0] ?? ''}` : ''
              differences.push(`seed ${String(seed)} kept ${String(kept)} row ${String(row._id)} reader ${reader.userId} member ${String(member)}${as}: each ${a}, alone ${b}`)
            }
          })
        }
      }
    }
    expect(differences, differences.join('\n')).to.deep.equal([])
    expect(compared).to.be.greaterThan(WORLDS * PEOPLE.length)
    for (const kind of ['visible', 'hidden', 'consented', 'unchecked']) expect(seen.get(kind) ?? 0, kind).to.be.greaterThan(0)
  })
})

describe('visibleToEach reads once for everyone, and asks each reader alone, a few at a time', () => {
  afterEach(() => visibility.resetVisibility())

  /** A world with an answer that names a record, an earlier answer and a share: there is something to read and to ask. */
  const busyWorld = () => {
    for (let seed = 1; seed < 500; seed += 1) {
      const world = makeWorld(seed)
      const row = world.rows.find((r) => {
        if (!isAnswer(r) || typeof r.labelVer !== 'string') return false
        const label = r.provenance as { direct?: { records?: unknown[] }; parents?: unknown[] } | undefined
        return (label?.direct?.records?.length ?? 0) > 0 && (label?.parents?.length ?? 0) > 0
      })
      if (row) return { world, row }
    }
    throw new Error('no world with a labelled answer naming records and parents')
  }

  it('one read of the labels, the closure and the shares for 40 people; one access request each, never more than 8 at once', async () => {
    const { world, row } = busyWorld()
    const base = depsOf(world)
    const store = memoryClosureStore(world.store)
    const calls = { labels: 0, readable: 0 }
    let inFlight = 0
    let mostInFlight = 0
    const deps: Partial<VisibilityDeps> = {
      ...base,
      closures: store as never,
      labels: async (org, ids) => {
        calls.labels += 1
        return (base.labels as VisibilityDeps['labels'])(org, ids)
      },
      readable: async (who, ids, timeoutMs, known, appConfig) => {
        calls.readable += 1
        inFlight += 1
        mostInFlight = Math.max(mostInFlight, inFlight)
        await new Promise((resolve) => setTimeout(resolve, 2))
        inFlight -= 1
        return (base.readable as VisibilityDeps['readable'])(who, ids, timeoutMs, known, appConfig)
      },
    }
    const readers: Reader[] = Array.from({ length: 40 }, (_, i) => ({ userId: PEOPLE[i % PEOPLE.length] as string, orgId: ORG }))
    const session = world.sessions.get(String(row.sessionId))
    const once = await visibility.visibleToEach(readers.slice(0, 1), session, row as ReaderRow, { deps, member: true, labelsLoaded: true })
    expect(once).to.have.length(1)
    const one = { labels: calls.labels, gets: store.counts.gets, shares: store.counts.shares }
    visibility.resetVisibility()
    calls.labels = 0
    calls.readable = 0
    store.counts.gets = 0
    store.counts.shares = 0
    store.docs.clear()
    const each = await visibility.visibleToEach(readers, session, row as ReaderRow, { deps, member: true, labelsLoaded: true })
    expect(each).to.have.length(40)
    // Forty readers read what one reader reads, and ask the access check forty times.
    expect({ labels: calls.labels, gets: store.counts.gets, shares: store.counts.shares }).to.deep.equal(one)
    expect(calls.readable).to.equal(40)
    expect(mostInFlight).to.be.within(2, 8)
  })
})
