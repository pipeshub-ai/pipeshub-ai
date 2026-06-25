import { expect } from 'chai'
import { mapWithConcurrency } from '../../../src/libs/utils/concurrency.util'

const tick = (ms: number) => new Promise((r) => setTimeout(r, ms))

describe('concurrency.util', () => {
  describe('mapWithConcurrency', () => {
    it('returns results in INPUT order even when items finish out of order', async () => {
      // Item 0 is slowest, item 3 fastest — result order must still be 0..3.
      const delays = [40, 10, 30, 5]
      const out = await mapWithConcurrency(delays, 2, async (d, i) => {
        await tick(d)
        return i
      })
      expect(out).to.deep.equal([0, 1, 2, 3])
    })

    it('never runs more than `limit` mappers at once', async () => {
      let active = 0
      let peak = 0
      const items = Array.from({ length: 12 }, (_, i) => i)
      await mapWithConcurrency(items, 5, async () => {
        active += 1
        peak = Math.max(peak, active)
        await tick(10)
        active -= 1
      })
      expect(peak).to.be.at.most(5)
      expect(peak).to.be.at.least(2) // genuinely ran in parallel, not serial
    })

    it('does NOT let one slow item block the others (the mixed-batch fix)', async () => {
      // One very slow "big file" + many fast "small files". With a real pool the
      // small files must finish well before the slow one — not wait behind it.
      const completionOrder: number[] = []
      const items = [500, 5, 5, 5, 5] // index 0 = the big file
      await mapWithConcurrency(items, 3, async (d, i) => {
        await tick(d)
        completionOrder.push(i)
      })
      // The slow item (0) completes LAST, after all the small ones.
      expect(completionOrder[completionOrder.length - 1]).to.equal(0)
      expect(completionOrder.slice(0, 4).sort()).to.deep.equal([1, 2, 3, 4])
    })

    it('clamps the worker count and handles an empty array', async () => {
      expect(await mapWithConcurrency([], 5, async () => 1)).to.deep.equal([])
      // limit larger than the item count is fine (clamped to length).
      const out = await mapWithConcurrency([1, 2], 99, async (n) => n * 2)
      expect(out).to.deep.equal([2, 4])
    })

    it('rejects if a mapper rejects', async () => {
      let err: Error | undefined
      try {
        await mapWithConcurrency([1, 2, 3], 2, async (n) => {
          if (n === 2) throw new Error('boom')
          return n
        })
      } catch (e) {
        err = e as Error
      }
      expect(err?.message).to.equal('boom')
    })

    it('stops starting new items after the first rejection (abort-on-error)', async () => {
      const started: number[] = []
      const items = Array.from({ length: 20 }, (_, i) => i)
      try {
        await mapWithConcurrency(items, 2, async (n) => {
          started.push(n)
          await tick(5)
          if (n === 1) throw new Error('early fail')
          return n
        })
      } catch {
        // expected
      }
      // With only 2 workers and an early failure at item 1, nowhere near all 20
      // items should have been started.
      expect(started.length).to.be.lessThan(items.length)
    })
  })
})
