import 'reflect-metadata'
import { expect } from 'chai'
import { FENCE_WAIT_QUEUE_MS, fenceCutoverWaitMs } from '../../../src/libs/repositories/fence'
import { appClients, describeClients, fenceTuning, sleep } from '../../../src/scripts/docstore-migrate/fence'
import { nonObjectIdSample } from '../../../src/scripts/docstore-migrate/preflight'

/** A runtime whose MongoDB answers every `$currentOp` aggregate with `rows`. */
const rtWith = (rows: unknown[], options: Record<string, unknown> = {}, mongoUri = 'mongodb://localhost:27017/es') => {
  const admin = { aggregate: () => ({ toArray: async () => rows }) }
  return {
    cfg: { mongoUri },
    mongo: { db: () => admin },
    db: () => ({ client: { options, db: () => admin } }),
  } as never
}

describe('docstore-migrate fence helpers', () => {
  const saved = { hooks: process.env.MIGRATE_TEST_HOOKS, opts: process.env.MIGRATE_FENCE_CLIENT_OPTIONS }
  afterEach(() => {
    for (const [k, v] of [['MIGRATE_TEST_HOOKS', saved.hooks], ['MIGRATE_FENCE_CLIENT_OPTIONS', saved.opts]] as const)
      if (v === undefined) delete process.env[k]
      else process.env[k] = v
  })

  it('the dwell is computed from the application\'s client options, not this tool\'s own client', () => {
    // this tool's client does not retry writes and waits 8 s for a server; the application retries and waits 5 s
    const tool = { connectTimeoutMS: 10000, serverSelectionTimeoutMS: 8000, retryWrites: false, waitQueueTimeoutMS: 0 }
    const o = fenceTuning.clientOptions(rtWith([], tool))
    expect(o).to.deep.equal({ waitQueueTimeoutMS: FENCE_WAIT_QUEUE_MS, connectTimeoutMS: 30000, serverSelectionTimeoutMS: 5000, retryWrites: true })
    // 2.5 s for every writer to see the fence, then twice (a retry) the 30 s queue, 30 s connect and 5 s selection
    expect(fenceCutoverWaitMs(o)).to.equal(132_500)
  })

  it('options in the URI that the application would also read count: a URI without write retries halves the send bound', () => {
    const o = fenceTuning.clientOptions(rtWith([], {}, 'mongodb://localhost:27017/es?retryWrites=false&connectTimeoutMS=10000'))
    expect(o).to.include({ retryWrites: false, connectTimeoutMS: 10000, serverSelectionTimeoutMS: 5000 })
    expect(fenceCutoverWaitMs(o)).to.equal(2500 + 30000 + 10000 + 5000)
  })

  it('test hooks shorten the client options only when both variables are set', () => {
    process.env.MIGRATE_FENCE_CLIENT_OPTIONS = JSON.stringify({ waitQueueTimeoutMS: 100 })
    expect(fenceTuning.clientOptions(rtWith([]))).to.include({ waitQueueTimeoutMS: FENCE_WAIT_QUEUE_MS })
    process.env.MIGRATE_TEST_HOOKS = '1'
    expect(fenceTuning.clientOptions(rtWith([]))).to.deep.equal({ waitQueueTimeoutMS: 100 })
  })

  it('counts the operations $currentOp lists as fenced writes', async () => {
    expect(await fenceTuning.fencedOpsRunning(rtWith([{}, {}]))).to.equal(2)
    expect(await fenceTuning.fencedOpsRunning(rtWith([]))).to.equal(0)
  })

  it('logs one JSON line per event on stderr', () => {
    const write = process.stderr.write
    const lines: string[] = []
    process.stderr.write = ((s: string) => (lines.push(s), true)) as never
    try {
      fenceTuning.log('waiting', { running: 2 })
    } finally {
      process.stderr.write = write
    }
    expect(JSON.parse(lines[0]!)).to.deep.equal({ event: 'waiting', running: 2 })
  })

  it('sleep waits and resolves', async () => {
    const t = Date.now()
    await sleep.ms(20)
    expect(Date.now() - t).to.be.at.least(15)
  })

  it('finds the application\'s connections, by name or by Node driver, and not this tool, mongosh or other drivers', async () => {
    const found = await appClients(
      rtWith([
        { appName: 'pipeshub-app', client: '10.0.0.1:1' },
        { appName: 'pipeshub-slack', client: '10.0.0.2:1' },
        { appName: 'renamed-in-uri', client: '10.0.0.3:1', clientMetadata: { driver: { name: 'nodejs|mongosh' } } },
        { appName: 'hidden', client: '10.0.0.4:1', clientMetadata: { driver: { name: 'nodejs' } } },
        { appName: 'docstore-migrate', client: '10.0.0.5:1', clientMetadata: { driver: { name: 'nodejs' } } },
        { appName: 'backup', client: '10.0.0.6:1', clientMetadata: { driver: { name: 'PyMongo' } } },
        { client: '10.0.0.1:1', appName: 'pipeshub-app' },
      ]),
    )
    expect(found.map((c) => c.client)).to.deep.equal(['10.0.0.1:1', '10.0.0.2:1', '10.0.0.4:1'])
  })

  it('describes the connections, three at most', () => {
    expect(describeClients([{ appName: 'a', client: '1' }])).to.equal('1 application connection to MongoDB (1)')
    const four = ['1', '2', '3', '4'].map((client) => ({ appName: 'a', client }))
    expect(describeClients(four)).to.equal('4 application connections to MongoDB (1, 2, 3, …)')
    expect(describeClients([{ appName: 'only-name', client: '' }])).to.contain('only-name')
  })

  it('names the type of an _id that is not an ObjectId', async () => {
    const db = {
      collection: () => ({
        find: () => ({ limit: () => ({ toArray: async () => [{ _id: 'a'.repeat(24) }, { _id: 5 }, { _id: { _bsontype: 'UUID', toString: () => 'u' } }] }) }),
      }),
    } as never
    const sample = await nonObjectIdSample(db, 'users')
    expect(sample.map((s) => s.type)).to.deep.equal(['string', 'number', 'UUID'])
    expect(sample[0]!.value).to.equal('a'.repeat(24))
  })
})
