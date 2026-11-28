import { expect } from 'chai'
import {
  CircuitBreaker,
  CheckOutcome,
  timeoutOutcome,
} from '../../../../src/modules/enterprise_search/utils/access-check-breaker'

/**
 * The breaker around the viewer access check: while the query service keeps failing, reads stop
 * asking it (they fail closed at once) instead of each waiting out its deadline.
 */
describe('the access check breaker', () => {
  let now = 0
  const breaker = () => new CircuitBreaker({ now: () => now })
  const check = (b: CircuitBreaker, outcome: CheckOutcome): void => {
    const ticket = b.allow()
    expect(ticket, 'a closed breaker lets the check through').to.not.equal(undefined)
    b.record(ticket!, outcome)
  }

  beforeEach(() => {
    now = 1_000_000
  })

  it('opens after five timeouts in a row', () => {
    const b = breaker()
    for (let i = 0; i < 4; i++) check(b, 'timeout')
    expect(b.allow()).to.not.equal(undefined)
    check(b, 'timeout')
    expect(b.allow()).to.equal(undefined)
  })

  it('a check that answers in between starts the count of timeouts again', () => {
    const b = breaker()
    for (let i = 0; i < 4; i++) check(b, 'timeout')
    check(b, 'ok')
    for (let i = 0; i < 4; i++) check(b, 'timeout')
    expect(b.allow()).to.not.equal(undefined)
  })

  it('opens when half of twenty or more checks in ten seconds failed', () => {
    const b = breaker()
    for (let i = 0; i < 10; i++) {
      check(b, 'ok')
      check(b, 'failed')
    }
    expect(b.allow()).to.equal(undefined)
  })

  it('stays closed under twenty checks, and with fewer than half failing', () => {
    const few = breaker()
    for (let i = 0; i < 9; i++) check(few, 'failed')
    check(few, 'ok')
    expect(few.allow()).to.not.equal(undefined)

    const most = breaker()
    for (let i = 0; i < 11; i++) check(most, 'ok')
    for (let i = 0; i < 9; i++) check(most, 'failed')
    expect(most.allow()).to.not.equal(undefined)
  })

  it('forgets failures older than ten seconds', () => {
    const b = breaker()
    for (let i = 0; i < 15; i++) check(b, 'failed')
    now += 10_001
    for (let i = 0; i < 6; i++) check(b, 'failed')
    for (let i = 0; i < 14; i++) check(b, 'ok')
    expect(b.allow()).to.not.equal(undefined)
  })

  it('after fifteen seconds open it lets one check through, and that check decides', () => {
    const b = breaker()
    for (let i = 0; i < 5; i++) check(b, 'timeout')
    now += 14_999
    expect(b.allow()).to.equal(undefined)
    now += 1
    const probe = b.allow()
    expect(probe).to.not.equal(undefined)
    expect(b.allow(), 'only one check while it probes').to.equal(undefined)
    b.record(probe!, 'ok')
    expect(b.allow()).to.not.equal(undefined)
  })

  it('a probe that fails keeps it open for another fifteen seconds', () => {
    const b = breaker()
    for (let i = 0; i < 5; i++) check(b, 'timeout')
    now += 15_000
    b.record(b.allow()!, 'failed')
    now += 14_999
    expect(b.allow()).to.equal(undefined)
    now += 1
    expect(b.allow()).to.not.equal(undefined)
  })

  it('a check sent before it opened does not close it when it answers', () => {
    const b = breaker()
    const early = b.allow()!
    for (let i = 0; i < 5; i++) check(b, 'timeout')
    now += 15_000
    const probe = b.allow()!
    b.record(early, 'ok')
    expect(b.allow(), 'still probing').to.equal(undefined)
    b.record(probe, 'failed')
    expect(b.allow()).to.equal(undefined)
  })

  it('reset closes it', () => {
    const b = breaker()
    for (let i = 0; i < 5; i++) check(b, 'timeout')
    b.reset()
    expect(b.allow()).to.not.equal(undefined)
  })

  it('a check that timed out for this process’s own reasons counts neither way', () => {
    const b = breaker()
    for (let i = 0; i < 4; i++) check(b, 'timeout')
    for (let i = 0; i < 30; i++) check(b, 'busy')
    expect(b.allow(), 'thirty of them open nothing').to.not.equal(undefined)
    check(b, 'timeout')
    expect(b.allow(), 'nor do they start the count of timeouts again').to.equal(undefined)
  })

  it('a probe that ends busy keeps it open, and lets the next check probe at once', () => {
    const b = breaker()
    for (let i = 0; i < 5; i++) check(b, 'timeout')
    now += 15_000
    b.record(b.allow()!, 'busy')
    const next = b.allow()
    expect(next?.probe, 'still open, probing again').to.equal(true)
    b.record(next!, 'ok')
    expect(b.allow()?.probe).to.equal(false)
  })

  it('a timeout counts against the service only when its request had time and its timer fired on time', () => {
    expect(timeoutOutcome(250, 99)).to.equal('timeout')
    expect(timeoutOutcome(249, 0), 'sent with too little of the deadline left').to.equal('busy')
    expect(timeoutOutcome(1_000, 100), 'its timer ran late: the event loop was the delay').to.equal('busy')
  })
})
