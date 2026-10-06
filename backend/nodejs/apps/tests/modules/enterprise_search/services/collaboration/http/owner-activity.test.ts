import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { FixedClock } from '../../../../../../src/libs/types/clock'
import { OwnerStatusUnavailableError } from '../../../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { OwnerActivity, OWNER_ACTIVITY_TTL_MS } from '../../../../../../src/modules/enterprise_search/services/collaboration/http/owner-activity'

const owner = (isDisabled: boolean) => ({ userId: 'u1', displayName: 'o', kind: 'human', isDisabled })

function setup() {
  const findByIds = sinon.stub()
  const warn = sinon.stub()
  const clock = new FixedClock(Date.parse('2026-01-01T00:00:00Z'))
  const activity = new OwnerActivity({ findByIds } as any, { warn } as any, clock)
  return { findByIds, warn, clock, activity }
}

describe('OwnerActivity', () => {
  afterEach(() => sinon.restore())

  it('a directory error throws the 503 error and logs a warning', async () => {
    const t = setup()
    t.findByIds.rejects(new Error('db down'))
    let caught: any
    try {
      await t.activity.isActive('o1', 'u1')
    } catch (e) {
      caught = e
    }
    expect(caught).to.be.instanceOf(OwnerStatusUnavailableError)
    expect(caught.statusCode).to.equal(503)
    expect(caught.code).to.equal('OWNER_STATUS_UNAVAILABLE')
    expect(t.warn.calledOnce).to.equal(true)
  })

  it('a failure is not cached: the next call asks the directory again', async () => {
    const t = setup()
    t.findByIds.onFirstCall().rejects(new Error('db down'))
    t.findByIds.onSecondCall().resolves([owner(false)])
    await t.activity.isActive('o1', 'u1').catch(() => undefined)
    expect(await t.activity.isActive('o1', 'u1')).to.equal(true)
    expect(t.findByIds.callCount).to.equal(2)
  })

  it('a success is cached until the TTL', async () => {
    const t = setup()
    t.findByIds.resolves([owner(false)])
    await t.activity.isActive('o1', 'u1')
    await t.activity.isActive('o1', 'u1')
    expect(t.findByIds.callCount).to.equal(1)
    t.clock.advance(OWNER_ACTIVITY_TTL_MS + 1)
    await t.activity.isActive('o1', 'u1')
    expect(t.findByIds.callCount).to.equal(2)
  })

  it('a disabled or missing owner is inactive', async () => {
    const t = setup()
    t.findByIds.resolves([owner(true)])
    expect(await t.activity.isActive('o1', 'u1')).to.equal(false)
    t.findByIds.resolves([])
    expect(await t.activity.isActive('o1', 'u2')).to.equal(false)
  })
})
