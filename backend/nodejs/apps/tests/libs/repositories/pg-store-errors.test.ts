import 'reflect-metadata'
import { expect } from 'chai'
import { FakePool, pgError } from '../db/fake-pool'
import { PgRuntime } from '../../../src/libs/db'
import { PgTx, PgUnitOfWork, assertVersionMatched, kernelTx, mapPgStoreError } from '../../../src/libs/repositories/pg'
import {
  ConflictError, DocStoreFencedError, DocStoreReadOnlyError, ForeignKeyError, UniqueConstraintError, VersionConflictError,
} from '../../../src/libs/repositories/errors'
import { mapMongoError } from '../../../src/libs/repositories/mongo'
import { GatewayTimeoutError, InternalServerError, ServiceUnavailableError } from '../../../src/libs/errors/http.errors'
import { ValidationError } from '../../../src/libs/errors/validation.error'
import express from 'express'
import type { AddressInfo } from 'node:net'
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware'
import { makeOrgContext, runWithOrgContext } from '../../../src/libs/context/orgContext'

const inOrg = <T>(fn: () => T) => runWithOrgContext(makeOrgContext({ orgId: 'o', userId: 'u', kind: 'user', source: 'jwt', requestId: 'r' }), fn)

describe('PostgreSQL errors become the seam errors', () => {
  const map = (code: string, extra: object = {}) => mapPgStoreError(Object.assign(pgError(code), extra))

  it('25006 and 55P04 are the cutover errors (503)', () => {
    const ro = map('25006') as DocStoreReadOnlyError
    expect(ro).to.be.instanceOf(DocStoreReadOnlyError)
    expect(ro.statusCode).to.equal(503)
    expect(ro.metadata?.retryAfter).to.equal('5')
    expect(map('55P04')).to.be.instanceOf(DocStoreFencedError)
  })

  it('25P02 and 42501 are internal errors (a swallowed 23505 without a savepoint, a wrong-org write)', () => {
    expect(map('25P02')).to.be.instanceOf(InternalServerError)
    expect(map('42501')).to.be.instanceOf(InternalServerError)
  })

  it('23505 is UniqueConstraintError{key} with column names only, like the Mongo mapping, and no values', () => {
    const e = map('23505', { constraint: 'users_pkey', detail: 'Key (org_id, email)=(abc, a@b.c) already exists.' }) as UniqueConstraintError
    expect(e).to.be.instanceOf(UniqueConstraintError)
    expect(e.metadata?.key).to.equal('org_id,email')
    expect(JSON.stringify(e.toJSON())).to.not.contain('a@b.c')
    const mongo = mapMongoError({ code: 11000, keyPattern: { orgId: 1, email: 1 } }) as UniqueConstraintError
    expect(mongo.constructor).to.equal(e.constructor)
    expect(mongo.statusCode).to.equal(e.statusCode)
    expect(Object.keys(mongo.metadata ?? {})).to.include('key')
  })

  it('23503, locks, timeouts, checks and connection loss', () => {
    expect(map('23503')).to.be.instanceOf(ForeignKeyError)
    expect(map('55P03')).to.be.instanceOf(ConflictError)
    expect(map('57014')).to.be.instanceOf(GatewayTimeoutError)
    expect(map('23514')).to.be.instanceOf(ValidationError)
    expect(map('08006')).to.be.instanceOf(ServiceUnavailableError)
    for (const c of ['53300', '53400', '25P03']) expect(map(c)).to.be.instanceOf(ServiceUnavailableError)
    for (const c of ['22021', '22P05', '22001', '22P02', '23502']) expect(map(c)).to.be.instanceOf(ValidationError)
  })

  it('anything else passes through unchanged', () => {
    const e = new Error('boom')
    expect(mapPgStoreError(e)).to.equal(e)
  })

  it('a version-guarded update that matched nothing is the same error Mongoose VersionError becomes', () => {
    expect(() => assertVersionMatched(0, 'doc')).to.throw(VersionConflictError)
    expect(() => assertVersionMatched(1, 'doc')).to.not.throw()
    expect(mapMongoError({ name: 'VersionError' })).to.be.instanceOf(VersionConflictError)
  })
})

describe('PgUnitOfWork', () => {
  const rt = (pool: FakePool) =>
    new PgRuntime({ pool, defaultService: 'node', sleep: async () => {}, fence: { fn: 'kernel.assert_store_epoch', epoch: 4 } })

  it('runs a write unit with the fence epoch, and a read-only run without it', async () => {
    const w = new FakePool()
    await inOrg(() => new PgUnitOfWork(rt(w)).run(async () => 1))
    expect(w.prologue.values).to.include(true)
    expect(w.prologue.values).to.include(4)
    const r = new FakePool()
    await inOrg(() => new PgUnitOfWork(rt(r)).run(async () => 1, { readOnly: true }))
    expect(r.prologue.values).to.include(false)
    expect(r.calls[0]?.text).to.match(/READ ONLY/)
  })

  it('hands the callback an opaque PgTx whose kernel unit is reachable only through kernelTx', async () => {
    const pool = new FakePool()
    const seen = await inOrg(() => new PgUnitOfWork(rt(pool)).run(async (tx) => ({ store: tx.store, isPg: tx instanceof PgTx, k: kernelTx(tx).orgId })))
    expect(seen).to.deep.equal({ store: 'postgres', isPg: true, k: 'o' })
    expect(() => kernelTx({ store: 'mongo' })).to.throw(InternalServerError)
  })

  it('maps the fence refusing the prologue to the seam errors', async () => {
    const closed = new FakePool((t) => { if (t.startsWith('WITH pre')) throw pgError('25006'); return {} })
    expect(await inOrg(() => new PgUnitOfWork(rt(closed)).run(async () => 1)).catch((e) => e)).to.be.instanceOf(DocStoreReadOnlyError)
    const stale = new FakePool((t) => { if (t.startsWith('WITH pre')) throw pgError('55P04'); return {} })
    expect(await inOrg(() => new PgUnitOfWork(rt(stale)).run(async () => 1)).catch((e) => e)).to.be.instanceOf(DocStoreFencedError)
  })

  it('re-runs the callback on a serialization failure (so it must be idempotent)', async () => {
    let n = 0
    const pool = new FakePool()
    const out = await inOrg(() => new PgUnitOfWork(rt(pool)).run(async () => { if (n++ === 0) throw pgError('40001'); return n }))
    expect(out).to.equal(2)
  })
})

describe('the freeze 503 through the error middleware', () => {
  it('carries a Retry-After header, for both stores', async () => {
    const app = express()
    app.get('/ro', () => { throw new DocStoreReadOnlyError(7) })
    app.get('/pg', () => { throw mapPgStoreError(pgError('25006')) })
    app.use(ErrorMiddleware.handleError())
    const server = await new Promise<import('node:http').Server>((r) => { const s = app.listen(0, '127.0.0.1', () => r(s)) })
    try {
      const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
      const a = await fetch(`${base}/ro`)
      expect([a.status, a.headers.get('retry-after')]).to.deep.equal([503, '7'])
      const b = await fetch(`${base}/pg`)
      expect([b.status, b.headers.get('retry-after')]).to.deep.equal([503, '5'])
    } finally {
      server.close()
    }
  })
})
