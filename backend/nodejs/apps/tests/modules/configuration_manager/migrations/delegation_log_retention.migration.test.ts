import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  DelegationLogRetentionMigration,
  DELEGATION_TTL_KEY,
} from '../../../../src/modules/configuration_manager/services/migrations/delegation_log_retention.migration'
import {
  DelegationEvent,
  DELEGATION_EVENT_RETENTION_DAYS,
} from '../../../../src/modules/oauth_provider/schema/delegation-event.schema'

/**
 * Changing the retention constant does not move an index that already exists.
 *
 * Mongoose builds indexes from the schema at boot, but MongoDB refuses a
 * `createIndex` that differs from an existing index only in
 * `expireAfterSeconds`, and Mongoose discards that error during model init. So
 * the service starts and the log quietly keeps whatever retention it had.
 * Every instance that has ever run would be unaffected by the new value, which
 * is exactly the case the change is for.
 */
describe('DelegationLogRetentionMigration', () => {
  const WANTED = DELEGATION_EVENT_RETENTION_DAYS * 86400

  let logger: any
  let command: sinon.SinonStub

  beforeEach(() => {
    logger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() }
    command = sinon.stub().resolves({ ok: 1 })
    sinon.stub(DelegationEvent, 'db').value({ db: { command } })
  })

  afterEach(() => sinon.restore())

  function stubIndexes(indexes: unknown[]) {
    sinon.stub(DelegationEvent.collection, 'indexes').resolves(indexes as any)
  }

  it('changes a stale TTL in place rather than leaving it', async () => {
    // What an instance upgraded from the previous retention actually has.
    stubIndexes([{ key: { createdAt: 1 }, expireAfterSeconds: 400 * 86400 }])

    const result = await new DelegationLogRetentionMigration(logger).run()

    expect(result.status).to.equal('updated')
    expect(result.previousSeconds).to.equal(400 * 86400)
    expect(result.currentSeconds).to.equal(WANTED)

    const sent = command.firstCall.args[0]
    expect(sent.collMod).to.equal(DelegationEvent.collection.collectionName)
    expect(sent.index.keyPattern).to.deep.equal(DELEGATION_TTL_KEY)
    expect(sent.index.expireAfterSeconds).to.equal(WANTED)
  })

  it('does nothing when the TTL already matches', async () => {
    stubIndexes([{ key: { createdAt: 1 }, expireAfterSeconds: WANTED }])

    const result = await new DelegationLogRetentionMigration(logger).run()

    expect(result.status).to.equal('already_correct')
    // Runs every boot, so doing nothing cheaply is the common path.
    expect(command.called).to.equal(false)
  })

  it('leaves a database that has no such index alone', async () => {
    // A fresh install: the schema builds the index with the right value.
    stubIndexes([{ key: { _id: 1 } }])

    const result = await new DelegationLogRetentionMigration(logger).run()

    expect(result.status).to.equal('no_index')
    expect(command.called).to.equal(false)
  })

  it('does not match a TTL index built on a different field', async () => {
    // Guards against reconciling the wrong index if another TTL is added.
    stubIndexes([{ key: { expiresAt: 1 }, expireAfterSeconds: 400 * 86400 }])

    const result = await new DelegationLogRetentionMigration(logger).run()

    expect(result.status).to.equal('no_index')
    expect(command.called).to.equal(false)
  })

  it('reports a missing collection rather than failing', async () => {
    sinon
      .stub(DelegationEvent.collection, 'indexes')
      .rejects(Object.assign(new Error('ns does not exist'), { code: 26 }))

    const result = await new DelegationLogRetentionMigration(logger).run()

    expect(result.status).to.equal('no_collection')
    expect(logger.error.called).to.equal(false)
  })

  it('starts the service even when the database refuses', async () => {
    stubIndexes([{ key: { createdAt: 1 }, expireAfterSeconds: 400 * 86400 }])
    command.rejects(new Error('not authorised'))

    const result = await new DelegationLogRetentionMigration(logger).run()

    // The old retention is wrong but safe; refusing to boot over it is worse.
    expect(result.status).to.equal('failed')
    expect(logger.error.called).to.equal(true)
  })
})
