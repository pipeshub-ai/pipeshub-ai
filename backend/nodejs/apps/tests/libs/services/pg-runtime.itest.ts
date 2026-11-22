/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import { randomBytes } from 'node:crypto'
import {
  PostgresDistributedKeyValueStore,
} from '../../../src/libs/keyValueStore/providers/PostgresDistributedKeyValueStore'
import {
  BasePgQueueConsumerConnection, BasePgQueueProducerConnection, PgQueueAdminService,
} from '../../../src/libs/services/pg-queue.service'
import { KeyAlreadyExistsError, KeyNotFoundError } from '../../../src/libs/errors/etcd.errors'
import { Logger } from '../../../src/libs/services/logger.service'
import { configurePg } from '../../../src/libs/db'
import { ensurePgRuntime } from '../../../src/libs/db/runtime-boot'

/**
 * The key-value store and the work queue on a real PostgreSQL. PG_RUNTIME_TEST_URL is the node login
 * (`postgresql://pipeshub_node_login@host:port/db`) of a database migrated to the heads, reached directly (no pooler).
 * The Python suite (tests/integration/pg_runtime) runs the same contract on the same tables.
 */
const URL = process.env.PG_RUNTIME_TEST_URL ?? ''
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const logger = Logger.getInstance({ service: 'pg-runtime-itest' })
const SER = (v: unknown) => Buffer.from(JSON.stringify(v))
const DE = (b: Buffer) => JSON.parse(b.toString())

class Producer extends BasePgQueueProducerConnection {}
class Consumer extends BasePgQueueConsumerConnection {}

async function until(cond: () => boolean, ms = 15000): Promise<void> {
  const end = Date.now() + ms
  while (!cond()) {
    if (Date.now() > end) throw new Error('condition not reached in time')
    await sleep(50)
  }
}

(URL ? describe : describe.skip)('PostgreSQL key-value store and work queue', function () {
  this.timeout(60000)
  let p: string
  before(() => {
    process.env.PG_NODE_URL = URL
    process.env.PG_POOLER = 'none'
    process.env.PG_QUEUE_RETRY_BASE_S = '0.1'
    process.env.MAX_DELIVERY_ATTEMPTS = '3'
    process.env.PG_LISTEN_URL = URL
  })
  beforeEach(() => { p = `/n${randomBytes(5).toString('hex')}/` })
  after(() => configurePg(undefined))

  describe('key-value store', () => {
    it('creates once, updates only what exists, deletes', async () => {
      const s = new PostgresDistributedKeyValueStore<unknown>(SER, DE)
      await s.createKey(`${p}k`, { n: 1 })
      let err: unknown
      try { await s.createKey(`${p}k`, { n: 2 }) } catch (e) { err = e }
      expect(err).to.be.instanceOf(KeyAlreadyExistsError)
      expect(await s.getKey(`${p}k`)).to.deep.equal({ n: 1 })
      await s.updateValue(`${p}k`, { n: 3 })
      expect(await s.getKey(`${p}k`)).to.deep.equal({ n: 3 })
      err = undefined
      try { await s.updateValue(`${p}missing`, 1) } catch (e) { err = e }
      expect(err).to.be.instanceOf(KeyNotFoundError)
      await s.deleteKey(`${p}k`)
      expect(await s.getKey(`${p}k`)).to.equal(null)
    })

    it('lists a directory and treats glob and SQL wildcards literally', async () => {
      const s = new PostgresDistributedKeyValueStore<unknown>(SER, DE)
      for (const k of ['d/a', 'd/b', 'd/sub/c', 'other', 'w/a*b', 'w/a%b', 'w/axb']) await s.createKey(p + k, 1)
      expect((await s.listKeysInDirectory(`${p}d`)).sort()).to.deep.equal([`${p}d/a`, `${p}d/b`, `${p}d/sub/c`])
      expect((await s.listKeysInDirectory(`${p}w`)).sort()).to.deep.equal([`${p}w/a%b`, `${p}w/a*b`, `${p}w/axb`])
      expect(await s.getAllKeys()).to.include(`${p}other`)
    })

    it('compareAndSet claims an absent key once and swaps only on a match', async () => {
      const s = new PostgresDistributedKeyValueStore<unknown>(SER, DE)
      expect(await s.compareAndSet(`${p}c`, null, 'a')).to.equal(true)
      expect(await s.compareAndSet(`${p}c`, null, 'b')).to.equal(false)
      expect(await s.compareAndSet(`${p}c`, 'x', 'b')).to.equal(false)
      expect(await s.compareAndSet(`${p}c`, 'a', 'b')).to.equal(true)
      expect(await s.getKey(`${p}c`)).to.equal('b')
    })

    it('fires watches for its own writes and delivers another store\'s published change', async () => {
      const a = new PostgresDistributedKeyValueStore<unknown>(SER, DE)
      const b = new PostgresDistributedKeyValueStore<unknown>(SER, DE)
      const seen: unknown[] = []
      await a.watchKey(`${p}w`, (v) => seen.push(v))
      await a.createKey(`${p}w`, 1)
      await a.updateValue(`${p}w`, 2)
      await a.deleteKey(`${p}w`)
      expect(seen).to.deep.equal([1, 2, null])
      const got: string[] = []
      await a.subscribeChanges((k) => got.push(k))
      await sleep(500)
      await b.publishCacheInvalidation(`${p}changed`)
      await until(() => got.includes(`${p}changed`))
      await a.disconnect()
    })

    it('shares its entries with the Python store: the bytes are the caller\'s serializer output', async () => {
      const s = new PostgresDistributedKeyValueStore<string>((v) => Buffer.from(v), (b) => b.toString())
      await s.createKey(`${p}raw`, '{"x":1}')
      expect(await s.getKey(`${p}raw`)).to.equal('{"x":1}')
    })
  })

  describe('work queue', () => {
    const topic = () => `t-${randomBytes(4).toString('hex')}`
    const msg = (i: number) => ({ key: `k${i}`, value: { eventType: 'test', payload: { i } } })
    const consumers: Consumer[] = []
    afterEach(async () => { for (const c of consumers.splice(0)) await c.disconnect() })

    async function consume(group: string, t: string, handler: (m: { value: any }) => Promise<void>, o: Record<string, unknown> = {}, fromBeginning = true) {
      const c = new Consumer({ groupId: group, clientId: 'c1', claimMinIdleMs: 30000, ...o }, logger)
      consumers.push(c)
      await c.subscribe([t], fromBeginning)
      await c.consume(handler as never)
      return c
    }

    it('delivers every message to one consumer of a group, once, and to every group', async () => {
      const t = topic()
      const a: number[] = []
      const b: number[] = []
      const other: number[] = []
      await consume('g', t, async (m) => { a.push(m.value.payload.i) })
      await consume('g', t, async (m) => { b.push(m.value.payload.i) })
      await consume('g2', t, async (m) => { other.push(m.value.payload.i) })
      const prod = new Producer(logger)
      await prod.publishBatch(t, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map(msg))
      await until(() => a.length + b.length === 10 && other.length === 10)
      await sleep(300)
      expect([...a, ...b].sort((x, y) => x - y)).to.deep.equal([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
      expect(other.sort((x, y) => x - y)).to.deep.equal([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
    })

    it('replays what is retained to a group that starts late, and only the tail to an ephemeral one', async () => {
      const t = topic()
      const prod = new Producer(logger)
      await consume('keep', t, async () => undefined)
      await prod.publish(t, msg(1))
      const late: number[] = []
      const tail: number[] = []
      await consume('late', t, async (m) => { late.push(m.value.payload.i) })
      await consume('eph', t, async (m) => { tail.push(m.value.payload.i) }, { ephemeral: true }, false)
      await prod.publish(t, msg(2))
      await until(() => late.length === 2 && tail.length === 1)
      expect(late.sort()).to.deep.equal([1, 2])
      expect(tail).to.deep.equal([2])
    })

    it('calls the handler again after it throws, and gives up after MAX_DELIVERY_ATTEMPTS', async () => {
      const t = topic()
      let flaky = 0
      await consume('f', t, async () => { flaky++; if (flaky === 1) throw new Error('boom') })
      let always = 0
      await consume('a', t, async () => { always++; throw new Error('always') })
      await new Producer(logger).publish(t, msg(1))
      await until(() => flaky >= 2 && always >= 3)
      await sleep(1000)
      expect(flaky).to.equal(2)
      expect(always).to.equal(3)
    })

    it('shares one LISTEN connection among the store and every consumer', async () => {
      const t = topic()
      const store = new PostgresDistributedKeyValueStore<unknown>(SER, DE)
      await store.subscribeChanges(() => undefined)
      for (const g of ['x1', 'x2', 'x3']) await consume(g, t, async () => undefined)
      const n = await ensurePgRuntime().withUow({ preOrg: true }, (tx) =>
        tx.query<{ n: string }>("/* pipeshub:test.listenconns */ SELECT count(*)::text AS n FROM pg_stat_activity WHERE application_name = 'pipeshub-listen'"),
      )
      expect(n.rows[0]?.n).to.equal('1')                    // one store subscription, three consumers, two channels: one connection
      await store.disconnect()
    })

    it('carries headers and lists its topics', async () => {
      const t = topic()
      let seen: Record<string, string> | undefined
      await consume('h', t, async (m: any) => { seen = m.headers })
      await new Producer(logger).publish(t, { ...msg(1), headers: { a: 'b' } })
      await until(() => seen !== undefined)
      expect(seen).to.deep.equal({ a: 'b' })
      expect(await new PgQueueAdminService().listTopics()).to.include(t)
    })
  })
})
