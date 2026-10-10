import { expect } from 'chai'
import sinon from 'sinon'
import {
  TurnLifecycle,
  statusFor,
} from '../../../../../../src/modules/enterprise_search/services/collaboration/turn/turn-lifecycle'
import { LeaseHandle } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/lease.types'

const fakeLease = (release: sinon.SinonStub = sinon.stub().resolves()) =>
  ({
    runId: 'r1',
    sessionId: 's1',
    release,
    stopHeartbeat: sinon.stub(),
  }) as unknown as LeaseHandle & { release: sinon.SinonStub; stopHeartbeat: sinon.SinonStub }

describe('TurnLifecycle', () => {
  it('maps every outcome to a session status', () => {
    expect(statusFor('completed')).to.equal('Complete')
    expect(statusFor('duplicate')).to.equal('Complete')
    expect(statusFor('unstarted')).to.equal('Complete')
    expect(statusFor('failed')).to.equal('Failed')
    expect(statusFor('stopped')).to.equal('Stopped')
    expect(statusFor('cancelled')).to.equal('Stopped')
  })

  it('stops the heartbeat, then releases with the outcome status', async () => {
    const lease = fakeLease()
    await new TurnLifecycle(lease).settle('failed')
    expect(lease.stopHeartbeat.calledBefore(lease.release)).to.equal(true)
    expect(lease.release.calledOnceWithExactly('Failed', { restorePrevious: false })).to.equal(true)
  })

  it('a turn that never ran asks the release to put the previous status back', async () => {
    for (const outcome of ['unstarted', 'duplicate'] as const) {
      const lease = fakeLease()
      await new TurnLifecycle(lease).settle(outcome)
      expect(lease.release.calledOnceWithExactly('Complete', { restorePrevious: true })).to.equal(true)
    }
  })

  it('acts once when settled twice, even with different outcomes', async () => {
    const lease = fakeLease()
    const turn = new TurnLifecycle(lease)
    await Promise.all([turn.settle('completed'), turn.settle('failed')])
    await turn.settle('stopped')
    expect(lease.release.calledOnceWithExactly('Complete', { restorePrevious: false })).to.equal(true)
  })

  it('never throws when the release fails, and logs it', async () => {
    const logger = { error: sinon.stub() }
    const lease = fakeLease(sinon.stub().rejects(new Error('db down')))
    await new TurnLifecycle(lease, logger).settle('completed')
    expect(logger.error.calledOnce).to.equal(true)
    expect(logger.error.firstCall.args[1]).to.include({ sessionId: 's1', runId: 'r1', error: 'db down' })
  })
})
