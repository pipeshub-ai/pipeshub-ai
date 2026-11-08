/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import { randomBytes } from 'node:crypto'
import { PgJob, PgJobQueue, PgJobWorker } from '../../../src/libs/services/pg-job-queue.service'
import { ensurePgRuntime } from '../../../src/libs/db/runtime-boot'
import { configurePg } from '../../../src/libs/db'
import { pgReconcilePending } from '../../../src/modules/crawling_manager/services/crawling_pg'
import sinon from 'sinon'
import { ConnectorScheduleSweepService, SWEEP_QUEUE_NAME } from '../../../src/modules/crawling_manager/services/connector_schedule_sweep'
import * as scheduledConnectorsClient from '../../../src/modules/crawling_manager/utils/scheduled_connectors_client'

/**
 * The PostgreSQL job scheduler (the BullMQ replacement for a deployment with no Redis). PG_RUNTIME_TEST_URL is the node login of a
 * database migrated to the heads, reached directly. Each test uses a queue of its own.
 */
const URL = process.env.PG_RUNTIME_TEST_URL ?? ''
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
async function until(cond: () => boolean | Promise<boolean>, ms = 15000): Promise<void> {
  const end = Date.now() + ms
  while (!(await cond())) {
    if (Date.now() > end) throw new Error('condition not reached in time')
    await sleep(25)
  }
}
type D = { n?: number; tag?: string }

(URL ? describe : describe.skip)('PostgreSQL job scheduler', function () {
  this.timeout(60000)
  let q: string
  const workers: PgJobWorker<D>[] = []
  before(() => {
    process.env.PG_NODE_URL = URL
    process.env.PG_POOLER = 'none'
    process.env.PG_LISTEN_URL = URL
  })
  beforeEach(() => { q = `q-${randomBytes(4).toString('hex')}` })
  afterEach(async () => { for (const w of workers.splice(0)) await w.close() })
  after(() => configurePg(undefined))

  const worker = (proc: (j: PgJob<D>) => Promise<unknown>, concurrency = 3) => {
    const w = new PgJobWorker<D>(q, proc, { concurrency, stalledInterval: 5000, maxStalledCount: 1 })
    workers.push(w)
    return w
  }

  it('fires a repeating schedule over and over, one run at a time per tick, and keeps a short history', async () => {
    const queue = new PgJobQueue<D>(q, { removeOnComplete: 2, attempts: 1 })
    const seen: string[] = []
    worker(async (j) => { seen.push(j.id) })
    await queue.add('crawl-x', { tag: 'a' }, { repeat: { every: 300 } })
    await until(() => seen.length >= 4)
    expect(new Set(seen).size).to.equal(seen.length)                          // no run fired twice
    const reps = await queue.getRepeatableJobs()
    expect(reps).to.have.length(1)
    expect(reps[0]).to.include({ name: 'crawl-x', every: '300' })
    await sleep(200)
    expect((await queue.getCompleted()).length).to.be.at.most(2)             // removeOnComplete: 2
  })

  it('removeOnComplete: true drops the finished job only, not the ones that finished before it', async () => {
    const queue = new PgJobQueue<D>(q, { attempts: 1 })
    const ran: string[] = []
    worker(async (j) => { ran.push(j.id) })
    await queue.add('keep', { n: 1 }, { jobId: 'kept' })
    await until(() => ran.includes('kept'))
    await until(async () => (await queue.getCompleted()).some((j) => j.id === 'kept'))
    await queue.add('drop', { n: 2 }, { jobId: 'dropped', removeOnComplete: true })
    await until(() => ran.includes('dropped'))
    await sleep(300)
    const ids = (await queue.getCompleted()).map((j) => j.id)
    expect(ids).to.include('kept')
    expect(ids).to.not.include('dropped')
  })

  it('a failed completion write does not retry the finished job or mark it failed', async () => {
    const queue = new PgJobQueue<D>(q, { attempts: 3 })
    let ran = 0
    const failed: string[] = []
    const errors: unknown[] = []
    const w = worker(async () => { ran++ })
    ;(w as unknown as { complete: () => Promise<boolean> }).complete = () => Promise.reject(new Error('completion write lost'))
    w.on('failed', (j: PgJob<D>) => failed.push(j.id))
    w.on('error', (e: unknown) => errors.push(e))
    const job = await queue.add('done-but-unrecorded', { n: 1 }, { jobId: 'unrecorded' })
    await until(() => errors.length >= 1)
    await sleep(300)
    expect(ran).to.equal(1)
    expect(failed).to.deep.equal([])
    expect(await job.getState()).to.equal('active')                          // left to stalled-job recovery, not retried or failed
  })

  it('runs a one-time delayed job once, and a job id that is taken adds nothing', async () => {
    const queue = new PgJobQueue<D>(q, { attempts: 1 })
    const ran: number[] = []
    worker(async (j) => { ran.push(j.data.n ?? -1) })
    const first = await queue.add('once', { n: 1 }, { delay: 400, jobId: 'crawl-once-1' })
    const again = await queue.add('once', { n: 2 }, { delay: 400, jobId: 'crawl-once-1' })
    expect(again.id).to.equal(first.id)
    expect(again.data.n).to.equal(1)                                          // the first one wins
    expect(await first.getState()).to.equal('delayed')
    await sleep(150)
    expect(ran).to.deep.equal([])
    await until(() => ran.length === 1)
    await sleep(500)
    expect(ran).to.deep.equal([1])
    await until(async () => (await first.getState()) === 'completed')
    expect((await queue.add('once', { n: 3 }, { jobId: 'crawl-once-1' })).data.n).to.equal(1)   // finished jobs still hold the id
  })

  it('removes and reschedules: the old schedule stops, the new one runs, removing twice says so', async () => {
    const queue = new PgJobQueue<D>(q, { attempts: 1 })
    const byTag: Record<string, number> = {}
    worker(async (j) => { byTag[j.data.tag ?? '?'] = (byTag[j.data.tag ?? '?'] ?? 0) + 1 })
    await queue.add('c', { tag: 'old' }, { repeat: { every: 250 } })
    await until(() => (byTag.old ?? 0) >= 2)
    expect(await queue.removeRepeatable('c', { every: 250 })).to.equal(true)
    expect(await queue.removeRepeatable('c', { every: 250 })).to.equal(false)
    expect(await queue.getRepeatableJobs()).to.have.length(0)
    expect((await queue.getJobs(['delayed', 'waiting'])).length).to.equal(0)   // its queued run went with it
    await sleep(300)
    const after = byTag.old
    await queue.add('c', { tag: 'new' }, { repeat: { every: 250 } })
    await until(() => (byTag.new ?? 0) >= 2)
    expect(byTag.old).to.be.at.most((after ?? 0) + 1)                          // at most the run that was already claimed
    expect(await queue.getRepeatableJobs()).to.have.length(1)
  })

  it('rejects a schedule that never produces a run time', async () => {
    const queue = new PgJobQueue<D>(q)
    let err: unknown
    try { await queue.add('bad', {}, { repeat: { pattern: 'not a cron', tz: 'UTC' } }) } catch (e) { err = e }
    expect(err).to.be.instanceOf(Error)
  })

  it('survives a restart: a new queue and worker carry on, and re-adding the same schedule duplicates nothing', async () => {
    const q1 = new PgJobQueue<D>(q, { attempts: 1 })
    const runs: string[] = []
    const w1 = worker(async (j) => { runs.push(`1:${j.id}`) })
    await q1.add('c', { tag: 't' }, { repeat: { every: 300 } })
    await until(() => runs.length >= 2)
    await w1.close()                                                           // the process goes away
    const before = runs.length
    const q2 = new PgJobQueue<D>(q, { attempts: 1 })                           // the next process
    worker(async (j) => { runs.push(`2:${j.id}`) })
    // the start-up reconcile re-adds every schedule; it must not queue a second chain
    for (let i = 0; i < 3; i++) await q2.add('c', { tag: 't' }, { repeat: { every: 300 } })
    await until(() => runs.length >= before + 3)
    expect(runs.some((r) => r.startsWith('2:'))).to.equal(true)
    expect(new Set(runs.map((r) => r.split(':').slice(1).join(':'))).size).to.equal(runs.length)   // no run twice
    expect((await q2.getRepeatableJobs()).length).to.equal(1)
    expect((await q2.getJobs(['delayed', 'waiting'])).length).to.be.at.most(1)
  })

  for (const skew of [50, 200, 1500, -300]) {
    it(`keeps a schedule running when the Node clock is ${Math.abs(skew)} ms ${skew > 0 ? 'behind' : 'ahead of'} the database`, async () => {
      const real = Date.now
      Date.now = () => real() - skew
      const queue = new PgJobQueue<D>(q, { attempts: 1 })
      const runs: number[] = []
      worker(async () => { runs.push(real()) }, 1)
      try {
        await queue.add('c', {}, { repeat: { every: 400 } })
        await sleep(3500)
        expect(runs.length).to.be.at.least(5)                                // about one per 400 ms, whatever the skew
        expect((await queue.getJobs(['delayed', 'waiting'])).length).to.equal(1)   // the chain goes on
        const gaps = runs.slice(1).map((t, i) => t - runs[i]!)
        expect(Math.max(...gaps)).to.be.lessThan(1200)
      } finally {
        Date.now = real
        await queue.removeRepeatable('c', { every: 400 })
      }
    })
  }

  it('removing a schedule wins against a run being claimed: no deadlock, no more fires', async () => {
    for (let round = 0; round < 12; round++) {
      const queue = new PgJobQueue<D>(`${q}-${round}`, { attempts: 1 })
      const fired: number[] = []
      const w = new PgJobWorker<D>(`${q}-${round}`, async () => { fired.push(Date.now()) }, { concurrency: 2 })
      workers.push(w)
      await queue.add('c', {}, { repeat: { every: 30 } })
      await sleep(40 + ((round * 17) % 60))                                       // land at different points of the claim cycle
      expect(await queue.removeRepeatable('c', { every: 30 })).to.equal(true)    // never rejects with a deadlock
      await sleep(250)
      const after = fired.length
      await sleep(300)
      expect(fired.length).to.equal(after)                                        // nothing fires after the remove returned
      expect((await queue.getJobs(['delayed', 'waiting'])).length).to.equal(0)   // and nothing is left queued
      expect(await queue.getRepeatableJobs()).to.have.length(0)
    }
  })

  it('two workers racing for the same runs start each exactly once', async () => {
    const queue = new PgJobQueue<D>(q, { attempts: 1 })
    const by: Record<string, string[]> = { a: [], b: [] }
    worker(async (j) => { by.a!.push(j.id); await sleep(20) }, 3)
    worker(async (j) => { by.b!.push(j.id); await sleep(20) }, 3)
    for (let i = 0; i < 60; i++) await queue.add('job', { n: i }, { jobId: `j${i}` })
    await until(() => by.a!.length + by.b!.length >= 60)
    await sleep(400)
    const all = [...by.a!, ...by.b!]
    expect(all.length).to.equal(60)
    expect(new Set(all).size).to.equal(60)
    expect(by.a!.length).to.be.greaterThan(0)
    expect(by.b!.length).to.be.greaterThan(0)
  })

  it('two Node processes racing for the same runs start each exactly once', async function () {
    const { spawn } = await import('node:child_process')
    const queue = new PgJobQueue<D>(q, { attempts: 1 })
    const mine: string[] = []
    const theirs: string[] = []
    const child = spawn(process.execPath, ['-r', 'reflect-metadata', '-r', 'ts-node/register', 'tests/libs/services/pg-job-worker.child.ts'], {
      env: { ...process.env, SCHED_TEST_QUEUE: q, TS_NODE_PROJECT: 'tsconfig.test.json' }, stdio: ['ignore', 'pipe', 'inherit'],
    })
    try {
      let ready = false
      child.stdout.on('data', (b: Buffer) => {
        for (const line of b.toString().split('\n')) {
          if (line === 'READY') ready = true
          else if (line.startsWith('RUN ')) theirs.push(line.slice(4))
        }
      })
      await until(() => ready, 40000)
      worker(async (j) => { mine.push(j.id); await sleep(20) }, 3)
      await sleep(300)
      for (let i = 0; i < 60; i++) await queue.add('job', { n: i }, { jobId: `p${i}` })
      await until(() => mine.length + theirs.length >= 60)
      await sleep(500)
      expect(mine.length + theirs.length).to.equal(60)
      expect(new Set([...mine, ...theirs]).size).to.equal(60)
      expect(mine.length).to.be.greaterThan(0)
      expect(theirs.length).to.be.greaterThan(0)
    } finally {
      child.kill('SIGTERM')
    }
  })

  it('retries a failing job with backoff, then marks it failed with the reason', async () => {
    const queue = new PgJobQueue<D>(q, { attempts: 3, backoff: { type: 'fixed', delay: 100 }, removeOnFail: 10 })
    const tries: Record<string, number> = {}
    const events: string[] = []
    const w = worker(async (j) => {
      tries[j.id] = (tries[j.id] ?? 0) + 1
      if (j.data.tag === 'flaky' && tries[j.id]! < 2) throw new Error('first try fails')
      if (j.data.tag === 'dead') throw new Error('always fails')
    })
    w.on('failed', (j: PgJob<D>) => events.push(`failed:${j.data.tag}`))
    w.on('completed', (j: PgJob<D>) => events.push(`completed:${j.data.tag}`))
    const flaky = await queue.add('f', { tag: 'flaky' })
    const dead = await queue.add('d', { tag: 'dead' })
    await until(async () => (await flaky.getState()) === 'completed' && (await dead.getState()) === 'failed')
    expect(tries[flaky.id]).to.equal(2)
    expect(tries[dead.id]).to.equal(3)
    const [failed] = await queue.getFailed()
    expect(failed?.failedReason).to.equal('always fails')
    expect(failed?.attemptsMade).to.equal(3)
    expect(events.filter((e) => e === 'failed:dead')).to.have.length(3)
  })

  it('puts back a run whose worker died, and cannot remove a run that is being processed', async () => {
    const queue = new PgJobQueue<D>(q, { attempts: 1 })
    const job = await queue.add('stuck', { n: 1 })
    // a worker that claimed it and died: active, claim already expired
    await ensurePgRuntime().withUow({ preOrg: true, write: true }, (tx) =>
      tx.query(
        `/* pipeshub:test.stuck */ UPDATE platform.sched_jobs SET state = 'active', attempts_made = 1, locked_by = 'dead', locked_until = now() - interval '1 second' WHERE queue = $1 AND id = $2`,
        [q, job.id],
      ),
    )
    const stalled: string[] = []
    const ran: string[] = []
    const w = worker(async (j) => { ran.push(j.id); await sleep(400) })
    w.on('stalled', (id: string) => stalled.push(id))
    await until(() => ran.length === 1)
    expect(stalled).to.deep.equal([job.id])
    const active = (await queue.getActive())[0]
    let err: unknown
    try { await active?.remove() } catch (e) { err = e }
    expect(err).to.be.instanceOf(Error)
    await until(async () => (await job.getState()) === 'completed')
  })

  it('keeps the reconcile-pending marks: a newer save keeps its mark, an older one clears it', async () => {
    const id = `c-${randomBytes(3).toString('hex')}`
    await pgReconcilePending.mark(id, 'org1')
    const [m] = (await pgReconcilePending.list()).filter((x) => x.connectorId === id)
    expect(m?.orgId).to.equal('org1')
    await pgReconcilePending.clear(id, (m?.at ?? 0) - 1)          // a save after `since` set the mark: it stays
    expect((await pgReconcilePending.list()).some((x) => x.connectorId === id)).to.equal(true)
    await pgReconcilePending.clear(id, (m?.at ?? 0) + 1)
    expect((await pgReconcilePending.list()).some((x) => x.connectorId === id)).to.equal(false)
  })

  it('wakes on NOTIFY, with no polling: a job added to an idle worker runs at once', async () => {
    const queue = new PgJobQueue<D>(q, { attempts: 1 })
    const ran: number[] = []
    worker(async (j) => { ran.push(j.data.n ?? 0) })
    await sleep(800)                                                            // idle: asleep until the 5-minute safety net
    const t0 = Date.now()
    await queue.add('now', { n: 7 })
    await until(() => ran.length === 1, 3000)
    expect(Date.now() - t0).to.be.lessThan(2000)
    await queue.add('later', { n: 8 }, { delay: 700 })                          // the worker sleeps until exactly this run time
    await until(() => ran.length === 2, 3000)
  })

  it('removes a schedule by the key it was listed under, and upserts one under a fixed id', async () => {
    const queue = new PgJobQueue<D>(q, { attempts: 1 })
    await queue.add('c', { tag: 'x' }, { repeat: { every: 60_000 } })
    await queue.add('d', { tag: 'y' }, { repeat: { every: 60_000 } })
    const [c] = (await queue.getRepeatableJobs()).filter((r) => r.name === 'c')
    expect(await queue.removeRepeatableByKey(c.key)).to.equal(true)
    expect(await queue.removeRepeatableByKey(c.key)).to.equal(false)          // gone: says so
    expect((await queue.getRepeatableJobs()).map((r) => r.name)).to.deep.equal(['d'])

    await queue.upsertJobScheduler('sweep', { every: 60_000 }, { name: 'sweep' })
    await queue.upsertJobScheduler('sweep', { every: 60_000 }, { name: 'sweep' })      // same interval again (a restart): nothing new
    const runs = async () => (await queue.getJobs(['delayed', 'waiting'])).filter((j) => j.name === 'sweep')
    expect(await runs()).to.have.length(1)
    await queue.upsertJobScheduler('sweep', { every: 120_000 }, { name: 'sweep' })      // another interval replaces the schedule
    const sweeps = (await queue.getRepeatableJobs()).filter((r) => r.name === 'sweep')
    expect(sweeps.map((r) => Number(r.every))).to.deep.equal([120_000])
    expect(await runs()).to.have.length(1)                                   // the old schedule's queued run went with it
  })

  describe('the connector schedule sweep with no Redis', () => {
    let fetchAll: sinon.SinonStub
    let svc: ConnectorScheduleSweepService | undefined
    beforeEach(() => {
      process.env.CRAWL_SCHEDULER = 'postgres'
      process.env.CONNECTOR_SCHEDULE_SWEEP_INTERVAL_MINUTES = '7'
      fetchAll = sinon.stub(scheduledConnectorsClient, 'fetchAllScheduledConnectors')
    })
    afterEach(async () => {
      await svc?.close()
      const sweepQueue = new PgJobQueue(SWEEP_QUEUE_NAME)
      for (const r of await sweepQueue.getRepeatableJobs()) await sweepQueue.removeRepeatableByKey(r.key)
      sinon.restore()
      delete process.env.CRAWL_SCHEDULER
      delete process.env.CONNECTOR_SCHEDULE_SWEEP_INTERVAL_MINUTES
    })

    it('schedules itself on the sweep queue, runs the sweep at start, and a restart or another replica adds no second schedule', async () => {
      let sweeps = 0
      fetchAll.callsFake(async () => { sweeps++; return { items: [], partial: false } })
      const scheduler = { listRepeatableSchedules: async () => [], jobNameFor: () => 'x', removeRepeatableByKey: async () => true }
      svc = new ConnectorScheduleSweepService(scheduler as never, {} as never, {} as never)
      await svc.start()
      const queue = new PgJobQueue(SWEEP_QUEUE_NAME)
      const reps = await queue.getRepeatableJobs()
      expect(reps.map((r) => [r.name, Number(r.every)])).to.deep.equal([['sweep', 7 * 60_000]])
      await until(() => sweeps >= 1, 10000)                                   // the startup run
      const second = new ConnectorScheduleSweepService(scheduler as never, {} as never, {} as never)
      await second.start()                                                    // another replica: same schedule
      expect(await queue.getRepeatableJobs()).to.have.length(1)
      await second.close()
    })
  })
})
