import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { fakeReplicaSetSession } from '../../../controller/chat-test-harness'
import { fencedWrite } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/fenced-write'
import { LeaseHandle, LeaseLostError } from '../../../../../../src/modules/enterprise_search/services/collaboration/leases/lease.types'

const leaseRenewing = (held: boolean) =>
  ({ runId: 'r1', sessionId: 's1', renew: sinon.stub().resolves(held) }) as unknown as LeaseHandle & { renew: sinon.SinonStub }

describe('fencedWrite', () => {
  const previous = process.env.REPLICA_SET_AVAILABLE
  afterEach(() => {
    sinon.restore()
    if (previous === undefined) delete process.env.REPLICA_SET_AVAILABLE
    else process.env.REPLICA_SET_AVAILABLE = previous
  })

  describe('standalone', () => {
    beforeEach(() => {
      process.env.REPLICA_SET_AVAILABLE = 'false'
    })

    it('renews, then runs fn without a session', async () => {
      const lease = leaseRenewing(true)
      const fn = sinon.stub().resolves('done')
      expect(await fencedWrite(lease, fn)).to.equal('done')
      expect(lease.renew.calledBefore(fn)).to.equal(true)
      expect(fn.calledOnceWithExactly(null)).to.equal(true)
    })

    it('throws LeaseLostError and skips fn when the renew matches nothing', async () => {
      const fn = sinon.stub().resolves()
      let error: unknown
      await fencedWrite(leaseRenewing(false), fn).catch((e: unknown) => {
        error = e
      })
      expect(error).to.be.instanceOf(LeaseLostError)
      expect(fn.called).to.equal(false)
    })
  })

  describe('replica set', () => {
    beforeEach(() => {
      process.env.REPLICA_SET_AVAILABLE = 'true'
    })

    it('renews and runs fn in the same transaction session, then ends it', async () => {
      const session = fakeReplicaSetSession()
      sinon.stub(mongoose, 'startSession').resolves(session as never)
      const withTransaction = sinon.spy(session, 'withTransaction')
      const lease = leaseRenewing(true)
      const fn = sinon.stub().resolves(42)
      expect(await fencedWrite(lease, fn)).to.equal(42)
      expect(withTransaction.calledOnce).to.equal(true)
      expect(lease.renew.calledOnceWithExactly(session)).to.equal(true)
      expect(fn.calledOnceWithExactly(session)).to.equal(true)
      expect(session.hasEnded).to.equal(true)
    })

    it('throws LeaseLostError, skips fn and still ends the session when the renew fails', async () => {
      const session = fakeReplicaSetSession()
      sinon.stub(mongoose, 'startSession').resolves(session as never)
      const fn = sinon.stub().resolves()
      let error: unknown
      await fencedWrite(leaseRenewing(false), fn).catch((e: unknown) => {
        error = e
      })
      expect(error).to.be.instanceOf(LeaseLostError)
      expect(fn.called).to.equal(false)
      expect(session.hasEnded).to.equal(true)
    })

    it('propagates an fn failure and ends the session', async () => {
      const session = fakeReplicaSetSession()
      sinon.stub(mongoose, 'startSession').resolves(session as never)
      let error: unknown
      await fencedWrite(leaseRenewing(true), sinon.stub().rejects(new Error('boom'))).catch((e: unknown) => {
        error = e
      })
      expect((error as Error).message).to.equal('boom')
      expect(session.hasEnded).to.equal(true)
    })
  })
})
