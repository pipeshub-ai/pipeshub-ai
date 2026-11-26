import 'reflect-metadata'
import { expect } from 'chai'
import { FakePool } from '../db/fake-pool'
import { PgRuntime } from '../../../src/libs/db'
import { MixedTx, MixedUnitOfWork } from '../../../src/libs/repositories/mixed'
import { MongoTx, sessionOf } from '../../../src/libs/repositories/mongo'
import { PgUnitOfWork, kernelTx } from '../../../src/libs/repositories/pg'
import type { Tx, UnitOfWork } from '../../../src/libs/repositories/uow'
import { makeOrgContext, runWithOrgContext } from '../../../src/libs/context/orgContext'

const fakeSession = { id: 'session' } as never
const mongo: UnitOfWork = { run: (fn) => fn(new MongoTx(fakeSession)) }
const inOrg = <T>(fn: () => T) => runWithOrgContext(makeOrgContext({ orgId: 'o', userId: 'u', kind: 'user', source: 'jwt', requestId: 'r' }), fn)

describe('mixed-store unit of work (testing aid for a half-ported install)', () => {
  const unit = () => {
    const pool = new FakePool()
    return { pool, uow: new MixedUnitOfWork(mongo, new PgUnitOfWork(new PgRuntime({ pool, defaultService: 'node' }))) }
  }

  it('gives Mongo repositories their session and PostgreSQL repositories their unit, in one handle', async () => {
    const { uow, pool } = unit()
    const seen = await inOrg(() =>
      uow.run(async (tx: Tx) => {
        await kernelTx(tx).query('/* pipeshub:test.x */ SELECT 1')
        return { session: sessionOf(tx), pg: tx instanceof MixedTx && !!tx.pgTx }
      }),
    )
    expect(seen).to.deep.equal({ session: fakeSession, pg: true })
    expect(pool.userCalls).to.have.length(1)
  })

  it('opens no PostgreSQL unit before an organisation exists (sign-up), and then only Mongo repositories can be used', async () => {
    const { uow, pool } = unit()
    const out = await uow.run(async (tx: Tx) => {
      expect(sessionOf(tx)).to.equal(fakeSession)
      return (tx as MixedTx).pgTx
    })
    expect(out).to.equal(undefined)
    expect(pool.connects).to.equal(0)
  })

  it('a PostgreSQL repository still refuses a handle that holds no unit', async () => {
    const { uow } = unit()
    const e = await uow.run(async (tx: Tx) => kernelTx(tx)).catch((x: Error) => x)
    expect((e as Error).message).to.match(/another store/)
  })
})
