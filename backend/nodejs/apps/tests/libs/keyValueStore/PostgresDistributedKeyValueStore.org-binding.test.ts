import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as runtimeBoot from '../../../src/libs/db/runtime-boot'
import * as edition from '../../../src/edition'
import { PostgresDistributedKeyValueStore } from '../../../src/libs/keyValueStore/providers/PostgresDistributedKeyValueStore'

/** Which org the PostgreSQL key-value store's units bind: none by default, the ambient org for a key `kvKeyBindsOrg` claims. */
const ORG = 'a'.repeat(24)

function fakeRuntime() {
  const units: Array<{ write?: boolean; preOrg?: boolean }> = []
  const tx = {
    query: async (sql: string) => ({
      rows: sql.includes('kv.get') || sql.includes('kv.range') ? [] : [{ ok: true }],
      rowCount: 1,
    }),
  }
  sinon.stub(runtimeBoot, 'ensurePgRuntime').returns({
    withUow: (opts: { write?: boolean; preOrg?: boolean }, fn: (t: unknown) => Promise<unknown>) => {
      units.push(opts)
      return fn(tx)
    },
  } as unknown as ReturnType<typeof runtimeBoot.ensurePgRuntime>)
  return units
}

async function useEveryOperation(key: string): Promise<void> {
  const s = new PostgresDistributedKeyValueStore<unknown>((v) => Buffer.from(JSON.stringify(v)), (b) => JSON.parse(b.toString()))
  ;(s as unknown as { lastPurge: number }).lastPurge = Number.POSITIVE_INFINITY
  await s.createKey(key, 1)
  await s.updateValue(key, 2)
  await s.getKey(key)
  await s.compareAndSet(key, null, 3)
  await s.deleteKey(key)
  await s.listKeysInDirectory(key)
}

describe('PostgresDistributedKeyValueStore org binding', () => {
  afterEach(() => sinon.restore())

  it('binds no org for any key by default', async () => {
    for (const k of ['/services/aiModels', `${ORG}/services/aiModels`, '']) expect(edition.kvKeyBindsOrg(k)).to.equal(false)
    const units = fakeRuntime()
    await useEveryOperation(`${ORG}/services/aiModels`)
    expect(units).to.have.length(6)
    expect(units.every((u) => u.preOrg === true)).to.equal(true)
  })

  it('binds the ambient org only for the keys the hook claims', async () => {
    sinon.stub(edition, 'kvKeyBindsOrg').callsFake((key: string) => key.startsWith(`${ORG}/`))
    const units = fakeRuntime()
    await useEveryOperation(`${ORG}/services/aiModels`)
    expect(units).to.have.length(6)
    expect(units.every((u) => u.preOrg === undefined)).to.equal(true)
    units.length = 0
    await useEveryOperation('/services/aiModels')
    expect(units.every((u) => u.preOrg === true)).to.equal(true)
  })

  it('keeps the notify and health units unbound even when the hook claims every key', async () => {
    sinon.stub(edition, 'kvKeyBindsOrg').returns(true)
    const units = fakeRuntime()
    const s = new PostgresDistributedKeyValueStore<unknown>((v) => Buffer.from(JSON.stringify(v)), (b) => JSON.parse(b.toString()))
    await s.getAllKeys()
    expect(units).to.deep.equal([{ write: false }])         // '' is a listing prefix: the hook decides it
    units.length = 0
    await s.publishCacheInvalidation(`${ORG}/services/aiModels`)
    expect(await s.healthCheck()).to.equal(true)
    expect(units).to.deep.equal([{ preOrg: true, write: true }, { preOrg: true, write: false }])
  })
})
