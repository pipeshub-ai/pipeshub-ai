import 'reflect-metadata'
import { expect } from 'chai'
import { purgePaidResultsOlderThan } from '../../../../src/modules/storage/utils/paid-results-purge'

const ROOT = 'org1/PipesHub/paid-results/rec-1'
const key = (n: number): string => `${ROOT}/ocr_page/${n.toString(16).padStart(64, '0')}.json`

class Unavailable extends Error {}

interface Obj { lastModifiedMs: number; etag: string }

/** An in-memory store that deletes by version or not, with a hook awaited at every listing and every delete. */
const fakeAdapter = (opts: { conditional: boolean; inconclusive?: boolean }) => {
  const objects = new Map<string, Obj>()
  const log: string[] = []
  const hooks: { beforeDelete?: () => void; afterListing?: (n: number) => void } = {}
  let listings = 0
  const adapter = {
    conditionalCapabilities: async () => ({ conditionalDelete: opts.conditional, bump: opts.conditional, ...(opts.inconclusive && { inconclusive: true }) }),
    listObjects: async (_prefix: string, options?: { after?: string; limit?: number }) => {
      listings += 1
      const all = [...objects.entries()]
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .filter(([k]) => options?.after === undefined || k > options.after)
        .map(([k, v]) => ({ key: k, size: 1, lastModifiedMs: v.lastModifiedMs, etag: v.etag }))
      const limit = options?.limit ?? all.length
      const out = { objects: all.slice(0, limit), ...(all.length > limit && { next: all[limit - 1]!.key }) }
      hooks.afterListing?.(listings)
      return out
    },
    deleteObjects: async (keys: string[]) => {
      hooks.beforeDelete?.()
      for (const k of keys) { log.push(`plain:${k}`); objects.delete(k) }
    },
    deleteObjectsIfMatch: async (items: { key: string; etag: string }[]) => {
      hooks.beforeDelete?.()
      const changed: string[] = []
      for (const { key: k, etag } of items) {
        const o = objects.get(k)
        if (o !== undefined && o.etag !== etag) { changed.push(k); continue }
        log.push(`guarded:${k}`)
        objects.delete(k)
      }
      return { changed }
    },
  }
  return { adapter: adapter as never, objects, log, hooks, listings: () => listings }
}

describe('purgePaidResultsOlderThan', () => {
  const BEFORE = 10_000

  it('on a store that cannot delete by version, an object overwritten between the listing and the delete is kept', async () => {
    const s = fakeAdapter({ conditional: false })
    s.objects.set(key(1), { lastModifiedMs: 1000, etag: 'old1' })
    s.objects.set(key(2), { lastModifiedMs: 1000, etag: 'old2' })
    // The refresh writes key(1) again (after the cutoff) once the first listing is out.
    s.hooks.afterListing = (n) => {
      if (n === 1) s.objects.set(key(1), { lastModifiedMs: BEFORE, etag: 'refreshed' })
    }
    await purgePaidResultsOlderThan(s.adapter, ROOT, BEFORE, Unavailable)
    expect(s.objects.has(key(1)), 'the refresh\'s own write was removed').to.equal(true)
    expect(s.objects.has(key(2))).to.equal(false)
  })

  it('an object whose write time did not move but whose ETag did (a bump on a store with second-grained stamps) is kept too', async () => {
    const s = fakeAdapter({ conditional: false })
    s.objects.set(key(1), { lastModifiedMs: 1000, etag: 'old' })
    s.hooks.afterListing = (n) => {
      if (n === 1) s.objects.set(key(1), { lastModifiedMs: 1000, etag: 'bumped' })
    }
    await purgePaidResultsOlderThan(s.adapter, ROOT, BEFORE, Unavailable)
    expect(s.objects.has(key(1))).to.equal(true)
  })

  it('an object listed with no version is looked at again as well, on a store that deletes by version', async () => {
    const s = fakeAdapter({ conditional: true })
    s.objects.set(key(1), { lastModifiedMs: 1000, etag: 'e1' })
    s.hooks.afterListing = (n) => {
      if (n === 1) s.objects.set(key(1), { lastModifiedMs: BEFORE + 1, etag: 'e2' })
    }
    await purgePaidResultsOlderThan(s.adapter, ROOT, BEFORE, Unavailable)
    expect(s.objects.has(key(1))).to.equal(true)
    expect(s.log).to.deep.equal([])
  })

  it('a store that deletes by version names the listed version, so a replaced object is left', async () => {
    const s = fakeAdapter({ conditional: true })
    s.objects.set(key(1), { lastModifiedMs: 1000, etag: 'e1' })
    s.hooks.beforeDelete = () => s.objects.set(key(1), { lastModifiedMs: 2000, etag: 'e2' })
    await purgePaidResultsOlderThan(s.adapter, ROOT, BEFORE, Unavailable)
    expect(s.objects.get(key(1))?.etag).to.equal('e2')
  })

  it('a probe that could not tell deletes nothing and is retried by its caller', async () => {
    const s = fakeAdapter({ conditional: false, inconclusive: true })
    s.objects.set(key(1), { lastModifiedMs: 1000, etag: 'e1' })
    let thrown: unknown
    try { await purgePaidResultsOlderThan(s.adapter, ROOT, BEFORE, Unavailable) } catch (e) { thrown = e }
    expect(thrown).to.be.instanceOf(Unavailable)
    expect(s.objects.has(key(1))).to.equal(true)
  })

  it('a full page whose key range gains an insert between the two listings still purges every old object', async () => {
    for (const conditional of [false, true]) {
      const s = fakeAdapter({ conditional })
      for (const n of [3, 4, 5, 6]) s.objects.set(key(n), { lastModifiedMs: 1000, etag: `old${n}` })
      // After the first page ([3, 4]) is listed, a key that sorts before it is stored: the second listing of the same
      // range now holds [1, 3] and the old key 4 is no longer on it.
      s.hooks.afterListing = (n) => {
        if (n === 1) s.objects.set(key(1), { lastModifiedMs: BEFORE + 5, etag: 'new1' })
      }
      await purgePaidResultsOlderThan(s.adapter, ROOT, BEFORE, Unavailable, 2)
      expect([...s.objects.keys()].sort(), `conditional=${conditional}`).to.deep.equal([key(1)])
    }
  })

  it('a full page whose key range loses a key between the two listings still purges every old object', async () => {
    const s = fakeAdapter({ conditional: false })
    for (const n of [1, 2, 3, 4]) s.objects.set(key(n), { lastModifiedMs: 1000, etag: `old${n}` })
    s.hooks.afterListing = (n) => {
      if (n === 1) s.objects.delete(key(1))
    }
    await purgePaidResultsOlderThan(s.adapter, ROOT, BEFORE, Unavailable, 2)
    expect(s.objects.size).to.equal(0)
  })

  it('never removes the refresh marker, and removes what is older than the cutoff when nothing moved', async () => {
    const s = fakeAdapter({ conditional: false })
    s.objects.set(key(1), { lastModifiedMs: 1000, etag: 'e1' })
    s.objects.set(`${ROOT}/refreshed/marker.json`, { lastModifiedMs: 1000, etag: 'm' })
    s.objects.set(key(2), { lastModifiedMs: BEFORE, etag: 'new' })
    await purgePaidResultsOlderThan(s.adapter, ROOT, BEFORE, Unavailable)
    expect([...s.objects.keys()].sort()).to.deep.equal([key(2), `${ROOT}/refreshed/marker.json`].sort())
  })
})
