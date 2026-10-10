import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { MongoMutationRunner } from '../../../../../../src/modules/enterprise_search/services/collaboration/mutation/mutation-runner'
import { MutationEffects } from '../../../../../../src/modules/enterprise_search/services/collaboration/mutation/mutation-effects'
import { fakeReplicaSetSession } from '../../../controller/chat-test-harness'
import { RecordingAudit, RecordingNotifier } from '../../../helpers/collaboration-world'

describe('MongoMutationRunner', () => {
  afterEach(() => sinon.restore())

  it('standalone: runs the work once with no session', async () => {
    const start = sinon.stub(mongoose, 'startSession')
    const work = sinon.stub().callsFake(async (session: unknown) => (session === undefined ? 'plain' : 'wrong'))
    expect(await new MongoMutationRunner(false).run(work)).to.equal('plain')
    expect(work.calledOnce).to.equal(true)
    expect(start.called).to.equal(false)
  })

  it('replica set: runs inside one transaction, hands the work the session and ends it', async () => {
    const session = fakeReplicaSetSession()
    const withTransaction = sinon.spy(session, 'withTransaction')
    sinon.stub(mongoose, 'startSession').resolves(session as never)
    const seen: unknown[] = []
    const out = await new MongoMutationRunner(true).run(async (s) => {
      seen.push(s)
      return 7
    })
    expect(out).to.equal(7)
    expect(seen).to.deep.equal([session])
    expect(withTransaction.calledOnce).to.equal(true)
    expect(session.hasEnded).to.equal(true)
  })

  it('replica set: a failing step still ends the session and the error reaches the caller', async () => {
    const session = fakeReplicaSetSession()
    sinon.stub(mongoose, 'startSession').resolves(session as never)
    let error: Error | undefined
    try {
      await new MongoMutationRunner(true).run(async () => {
        throw new Error('step failed')
      })
    } catch (e) {
      error = e as Error
    }
    expect(error?.message).to.equal('step failed')
    expect(session.hasEnded).to.equal(true)
  })
})

describe('MutationEffects', () => {
  const event = { type: 'chat.unshared', orgId: 'o', sessionId: 's', actorUserId: 'a', ref: { kind: 'chat', conversationId: 's' }, principal: { type: 'user', userId: 'u' } } as never
  const identity = { userId: 'a', orgId: 'o', authHeaders: {}, requestKey: {} }

  it('within a transaction the audit row and the events carry the session and their failures abort it', async () => {
    const audit = new RecordingAudit()
    const notifier = new RecordingNotifier()
    const session = fakeReplicaSetSession() as never
    const effects = new MutationEffects(audit, notifier, { error: sinon.stub() })
    await effects.publish([event], session, identity)
    expect(notifier.options[0]?.session).to.equal(session)
    notifier.failWith = new Error('outbox down')
    let error: Error | undefined
    try {
      await effects.publish([event], session, identity)
    } catch (e) {
      error = e as Error
    }
    expect(error?.message).to.equal('outbox down')
  })

  it('standalone: a notifier failure is logged and swallowed; an empty batch publishes nothing', async () => {
    const notifier = new RecordingNotifier()
    const logger = { error: sinon.stub() }
    const effects = new MutationEffects(new RecordingAudit(), notifier, logger)
    await effects.publish([], undefined, identity)
    expect(notifier.batches).to.have.length(0)
    notifier.failWith = new Error('outbox down')
    await effects.publish([event], undefined, identity)
    expect(logger.error.calledOnce).to.equal(true)
    expect(JSON.stringify(logger.error.firstCall.args)).to.not.include('userId')
  })
})
