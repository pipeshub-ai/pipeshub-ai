import 'reflect-metadata'
import { expect } from 'chai'
import { usesPgScheduler } from '../../../../src/modules/crawling_manager/services/crawling_pg'

describe('crawl scheduler backend choice', () => {
  const keys = ['CRAWL_SCHEDULER', 'REDIS_MODE', 'KV_STORE_TYPE', 'MESSAGE_BROKER']
  let saved: Record<string, string | undefined>
  beforeEach(() => { saved = Object.fromEntries(keys.map((k) => [k, process.env[k]])); keys.forEach((k) => delete process.env[k]) })
  afterEach(() => { keys.forEach((k) => (saved[k] === undefined ? delete process.env[k] : (process.env[k] = saved[k]))) })

  it('stays on BullMQ when a Redis is configured', () => {
    expect(usesPgScheduler()).to.equal(false)
    process.env.KV_STORE_TYPE = 'postgres'                       // only one store is PostgreSQL: Redis is still in use
    expect(usesPgScheduler()).to.equal(false)
  })

  it('follows REDIS_MODE: postgres and inprocess mean no Redis, standalone and cluster mean one', () => {
    process.env.REDIS_MODE = 'postgres'
    expect(usesPgScheduler()).to.equal(true)
    process.env.REDIS_MODE = 'inprocess'
    expect(usesPgScheduler()).to.equal(true)
    process.env.REDIS_MODE = 'standalone'
    expect(usesPgScheduler()).to.equal(false)
    process.env.KV_STORE_TYPE = 'postgres'
    process.env.MESSAGE_BROKER = 'postgres'
    expect(usesPgScheduler()).to.equal(false)                  // an explicit Redis mode wins over the inference
  })

  it('moves to PostgreSQL when both stores are PostgreSQL, or when asked', () => {
    process.env.KV_STORE_TYPE = 'postgres'
    process.env.MESSAGE_BROKER = 'postgres'
    expect(usesPgScheduler()).to.equal(true)
    process.env.CRAWL_SCHEDULER = 'bullmq'                       // an explicit choice wins
    expect(usesPgScheduler()).to.equal(false)
    delete process.env.KV_STORE_TYPE
    process.env.CRAWL_SCHEDULER = 'postgres'
    expect(usesPgScheduler()).to.equal(true)
  })
})
