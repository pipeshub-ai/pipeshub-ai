import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { AuditEvent } from '../../../src/libs/audit/audit-event.schema'
import { MongoAuditWriter } from '../../../src/libs/audit/audit.writer'

const oid = () => new mongoose.Types.ObjectId()

describe('libs/audit/audit.writer', () => {
  let create: sinon.SinonStub
  const logger = { error: sinon.stub() }
  const event = () => ({
    orgId: oid(),
    actorUserId: oid(),
    action: 'chat.share',
    targetType: 'chatSession',
    targetId: 'abc',
    principal: { principalType: 'user', principalId: 'u1' },
    before: { accessLevel: 'read' },
    after: { accessLevel: 'write' },
    aclVersion: 7,
  })

  beforeEach(() => {
    create = sinon.stub(AuditEvent, 'create').resolves([] as never)
    logger.error.resetHistory()
  })
  afterEach(() => sinon.restore())

  it('writes one row with aclVersion merged into after', async () => {
    const e = event()
    await new MongoAuditWriter(logger).record(e)
    expect(create.calledOnce).to.equal(true)
    const [docs, opts] = create.firstCall.args
    expect(docs).to.have.length(1)
    expect(docs[0]).to.include({ action: 'chat.share', targetId: 'abc' })
    expect(docs[0].after).to.deep.equal({ accessLevel: 'write', aclVersion: 7 })
    expect(docs[0].before).to.deep.equal({ accessLevel: 'read' })
    expect(docs[0]).to.not.have.property('aclVersion')
    expect(opts).to.deep.equal({})
  })

  it('stores aclVersion alone when there is no other after', async () => {
    const { after: _after, ...e } = event()
    await new MongoAuditWriter(logger).record(e)
    expect(create.firstCall.args[0][0].after).to.deep.equal({ aclVersion: 7 })
  })

  it('passes the session through', async () => {
    const session = {} as mongoose.ClientSession
    await new MongoAuditWriter(logger).record(event(), { session })
    expect(create.firstCall.args[1]).to.deep.equal({ session })
  })

  it('on standalone a failure is logged at error and never thrown', async () => {
    create.rejects(new Error('disk full'))
    await new MongoAuditWriter(logger).record(event())
    expect(logger.error.calledOnce).to.equal(true)
    expect(logger.error.firstCall.args[1]).to.include({ action: 'chat.share', error: 'disk full' })
  })

  it('inside a transaction a failure propagates so the transaction aborts', async () => {
    create.rejects(new Error('txn aborted'))
    let thrown: unknown
    await new MongoAuditWriter(logger)
      .record(event(), { session: {} as mongoose.ClientSession })
      .catch((e: unknown) => {
        thrown = e
      })
    expect((thrown as Error).message).to.equal('txn aborted')
    expect(logger.error.called).to.equal(false)
  })

  it('exposes no update or delete API', () => {
    expect(Object.getOwnPropertyNames(MongoAuditWriter.prototype).sort()).to.deep.equal(['constructor', 'record'])
  })
})
