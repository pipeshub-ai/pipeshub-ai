import { expect } from 'chai'
import { matchesFilter } from '../../../controller/chat-test-harness'
import {
  LEASE_TTL_MS,
  leaseFree,
  leaseOwned,
  leaseRenewUpdate,
} from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/lease-filters'

const now = new Date('2026-10-02T10:00:00Z')
const at = (offsetMs: number) => new Date(now.getTime() + offsetMs)
const run = (leaseExpiresAt: Date, runId = 'r1') => ({ runId, leaseExpiresAt })

describe('lease filters', () => {
  describe('leaseFree', () => {
    it('PH05-01: matches a null, absent or expired lease and not a live one', () => {
      const filter = leaseFree(now)
      expect(matchesFilter({ activeRun: null }, filter)).to.equal(true)
      expect(matchesFilter({}, filter)).to.equal(true)
      expect(matchesFilter({ activeRun: run(at(-1000)) }, filter)).to.equal(true)
      expect(matchesFilter({ activeRun: run(at(1000)) }, filter)).to.equal(false)
    })

    it('treats a lease expiring exactly now as free', () => {
      expect(matchesFilter({ activeRun: run(now) }, leaseFree(now))).to.equal(true)
    })
  })

  describe('leaseOwned', () => {
    it('matches only the run that holds the lease', () => {
      expect(matchesFilter({ activeRun: run(at(1000), 'r1') }, leaseOwned('r1'))).to.equal(true)
      expect(matchesFilter({ activeRun: run(at(1000), 'r2') }, leaseOwned('r1'))).to.equal(false)
      expect(matchesFilter({ activeRun: null }, leaseOwned('r1'))).to.equal(false)
    })
  })

  describe('leaseRenewUpdate', () => {
    it('writes only activeRun.leaseExpiresAt, now plus the TTL', () => {
      expect(leaseRenewUpdate(now)).to.deep.equal({
        $set: { 'activeRun.leaseExpiresAt': at(LEASE_TTL_MS) },
      })
    })

    it('honours a custom ttl', () => {
      expect(leaseRenewUpdate(now, 5000)).to.deep.equal({
        $set: { 'activeRun.leaseExpiresAt': at(5000) },
      })
    })
  })
})
