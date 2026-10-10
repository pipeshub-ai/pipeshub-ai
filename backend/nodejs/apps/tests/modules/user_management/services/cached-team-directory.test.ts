import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  bumpTeamsVersion,
  CachedTeamDirectory,
  readTeamsVersion,
  teamIdsKey,
  teamsVersionKey,
  TEAM_IDS_CACHE_TTL_SECONDS,
} from '../../../../src/modules/user_management/services/cached-team-directory'
import { ITeamDirectory, TeamIdsResult } from '../../../../src/modules/user_management/services/team-directory.service'
import { ICacheService } from '../../../../src/libs/services/cache/cacheService.interface'
import { metricsBackend } from '../../../../src/libs/services/telemetry/metrics-backend'
import { CallerIdentity } from '../../../../src/libs/types/caller-identity'
import { createMockLogger } from '../../../helpers/mock-logger'

const identity = (userId = 'u1', orgId = 'o1'): CallerIdentity => ({
  userId,
  orgId,
  authHeaders: { authorization: 'Bearer t' },
  requestKey: {},
})

class MemoryCache implements ICacheService {
  readonly store = new Map<string, unknown>()
  readonly ttls = new Map<string, number | undefined>()
  async get<T>(key: string): Promise<T | null> {
    return (this.store.has(key) ? this.store.get(key) : null) as T | null
  }
  async set(key: string, value: unknown, options?: { ttl?: number }): Promise<void> {
    this.store.set(key, value)
    this.ttls.set(key, options?.ttl)
  }
  async delete(key: string): Promise<void> {
    this.store.delete(key)
  }
  async increment(key: string): Promise<number> {
    const next = Number(this.store.get(key) ?? 0) + 1
    this.store.set(key, next)
    return next
  }
  async disconnect(): Promise<void> {}
  isConnected(): boolean {
    return true
  }
}

const counter = async (result: string): Promise<number> => {
  const text = await metricsBackend.serialize()
  const m = text.match(new RegExp(`collab_teamids_cache_total\\{result="${result}"\\} (\\d+)`))
  return m ? Number(m[1]) : 0
}

const upstreamCount = async (result: string): Promise<number> => {
  const text = await metricsBackend.serialize()
  const m = text.match(new RegExp(`collab_teamids_upstream_seconds_count\\{result="${result}"\\} (\\d+)`))
  return m ? Number(m[1]) : 0
}

describe('CachedTeamDirectory', () => {
  let cache: MemoryCache
  let lookup: sinon.SinonStub
  let inner: ITeamDirectory
  let logger: ReturnType<typeof createMockLogger>
  let dir: CachedTeamDirectory

  beforeEach(() => {
    cache = new MemoryCache()
    lookup = sinon.stub().resolves({ status: 'ok', teamIds: ['t1', 't2'] } as TeamIdsResult)
    inner = { callerTeamIds: lookup, exists: sinon.stub().resolves(true), teamsVersion: sinon.stub().resolves(0) }
    logger = createMockLogger()
    dir = new CachedTeamDirectory(inner, () => cache, logger)
  })

  afterEach(() => sinon.restore())

  it('PERF-04: misses once, stores under the versioned key with a 30 s TTL, then hits', async () => {
    const hitsBefore = await counter('hit')
    const missesBefore = await counter('miss')

    expect(await dir.callerTeamIds(identity())).to.deep.equal({ status: 'ok', teamIds: ['t1', 't2'] })
    expect(await dir.callerTeamIds(identity())).to.deep.equal({ status: 'ok', teamIds: ['t1', 't2'] })

    expect(lookup.calledOnce).to.equal(true)
    const key = 'teamids:v1:o1:u1:0'
    expect(teamIdsKey('o1', 'u1', 0)).to.equal(key)
    expect(cache.store.get(key)).to.deep.equal(['t1', 't2'])
    expect(cache.ttls.get(key)).to.equal(TEAM_IDS_CACHE_TTL_SECONDS)
    expect(TEAM_IDS_CACHE_TTL_SECONDS).to.equal(30)
    expect(await counter('hit')).to.equal(hitsBefore + 1)
    expect(await counter('miss')).to.equal(missesBefore + 1)
  })

  it('PERF-04: times each upstream lookup by result; hits and coalesced calls add none', async () => {
    const okBefore = await upstreamCount('ok')
    const errorBefore = await upstreamCount('error')

    await dir.callerTeamIds(identity())
    await dir.callerTeamIds(identity())
    expect(await upstreamCount('ok')).to.equal(okBefore + 1)

    lookup.resolves({ status: 'unresolved' })
    await dir.callerTeamIds(identity('u2'))
    expect(await upstreamCount('error')).to.equal(errorBefore + 1)

    lookup.rejects(new Error('boom'))
    let thrown = false
    await dir.callerTeamIds(identity('u3')).catch(() => (thrown = true))
    expect(thrown).to.equal(true)
    expect(await upstreamCount('error')).to.equal(errorBefore + 2)
  })

  it('single-flight: concurrent misses for one key share one lookup', async () => {
    let release!: (r: TeamIdsResult) => void
    lookup.returns(new Promise<TeamIdsResult>((resolve) => (release = resolve)))
    const coalescedBefore = await counter('coalesced')

    const calls = Promise.all([dir.callerTeamIds(identity()), dir.callerTeamIds(identity()), dir.callerTeamIds(identity())])
    await new Promise((r) => setImmediate(r))
    release({ status: 'ok', teamIds: ['t1'] })
    const results = await calls

    expect(lookup.calledOnce).to.equal(true)
    for (const r of results) expect(r).to.deep.equal({ status: 'ok', teamIds: ['t1'] })
    expect(await counter('coalesced')).to.equal(coalescedBefore + 2)
  })

  it('single-flight does not merge different users and clears after settling', async () => {
    await Promise.all([dir.callerTeamIds(identity('u1')), dir.callerTeamIds(identity('u2'))])
    expect(lookup.calledTwice).to.equal(true)

    cache.store.clear()
    await dir.callerTeamIds(identity('u1'))
    expect(lookup.callCount).to.equal(3)
  })

  it('a cache error passes through to the connector, never denies, and does not write', async () => {
    const errorsBefore = await counter('error')
    sinon.stub(cache, 'get').rejects(new Error('redis down'))
    const set = sinon.spy(cache, 'set')

    expect(await dir.callerTeamIds(identity())).to.deep.equal({ status: 'ok', teamIds: ['t1', 't2'] })

    expect(lookup.calledOnce).to.equal(true)
    expect(set.called).to.equal(false)
    expect(logger.warn.calledOnce).to.equal(true)
    expect(await counter('error')).to.equal(errorsBefore + 1)
  })

  it('a failed cache write still returns the resolved ids', async () => {
    sinon.stub(cache, 'set').rejects(new Error('redis down'))
    expect(await dir.callerTeamIds(identity())).to.deep.equal({ status: 'ok', teamIds: ['t1', 't2'] })
    expect(logger.warn.calledOnce).to.equal(true)
  })

  it('never caches unresolved', async () => {
    lookup.resolves({ status: 'unresolved' })
    expect(await dir.callerTeamIds(identity())).to.deep.equal({ status: 'unresolved' })
    expect(cache.store.size).to.equal(0)

    lookup.resolves({ status: 'ok', teamIds: ['t1'] })
    expect(await dir.callerTeamIds(identity())).to.deep.equal({ status: 'ok', teamIds: ['t1'] })
    expect(lookup.calledTwice).to.equal(true)
  })

  it('caches an empty membership list', async () => {
    lookup.resolves({ status: 'ok', teamIds: [] })
    await dir.callerTeamIds(identity())
    expect(await dir.callerTeamIds(identity())).to.deep.equal({ status: 'ok', teamIds: [] })
    expect(lookup.calledOnce).to.equal(true)
  })

  it('treats a malformed cached value as a miss', async () => {
    cache.store.set('teamids:v1:o1:u1:0', { not: 'a list' })
    expect(await dir.callerTeamIds(identity())).to.deep.equal({ status: 'ok', teamIds: ['t1', 't2'] })
    expect(lookup.calledOnce).to.equal(true)
  })

  it('TM-03: a version bump changes the key so the next call misses', async () => {
    await dir.callerTeamIds(identity())
    await bumpTeamsVersion('o1', logger, cache)
    lookup.resolves({ status: 'ok', teamIds: ['t1', 't2', 't3'] })

    expect(await dir.callerTeamIds(identity())).to.deep.equal({ status: 'ok', teamIds: ['t1', 't2', 't3'] })

    expect(lookup.calledTwice).to.equal(true)
    expect(cache.store.has('teamids:v1:o1:u1:1')).to.equal(true)
    expect(await dir.teamsVersion('o1')).to.equal(1)
  })

  it('a bump for one org does not move another org', async () => {
    await bumpTeamsVersion('o1', logger, cache)
    expect(await dir.teamsVersion('o2')).to.equal(0)
    expect(teamsVersionKey('o1')).to.equal('teamids:ver:o1')
  })

  it('without a shared cache it passes straight through', async () => {
    const bare = new CachedTeamDirectory(inner, () => undefined, logger)
    await bare.callerTeamIds(identity())
    await bare.callerTeamIds(identity())
    expect(lookup.calledTwice).to.equal(true)
    expect(await bare.teamsVersion('o1')).to.equal(0)
  })

  it('delegates memberUserIds to the inner directory without caching it', async () => {
    const memberUserIds = sinon.stub().resolves({ status: 'ok', userIds: ['u'] })
    ;(inner as any).memberUserIds = memberUserIds
    expect(await dir.memberUserIds('t', identity(), { limit: 50 })).to.deep.equal({ status: 'ok', userIds: ['u'] })
    await dir.memberUserIds('t', identity(), { limit: 50 })
    expect(memberUserIds.calledTwice).to.equal(true)
  })

  it('delegates exists to the inner directory', async () => {
    expect(await dir.exists('t', identity())).to.equal(true)
  })
})

describe('teams version', () => {
  it('defaults to 0, and a read error is 0 rather than a throw', async () => {
    const cache = new MemoryCache()
    expect(await readTeamsVersion(cache, 'o1')).to.equal(0)
    expect(await readTeamsVersion(undefined, 'o1')).to.equal(0)
    sinon.stub(cache, 'get').rejects(new Error('down'))
    expect(await readTeamsVersion(cache, 'o1')).to.equal(0)
    sinon.restore()
  })

  it('bump never throws when the cache fails and logs a warning', async () => {
    const cache = new MemoryCache()
    sinon.stub(cache, 'increment').rejects(new Error('down'))
    const logger = createMockLogger()
    await bumpTeamsVersion('o1', logger, cache)
    expect(logger.warn.calledOnce).to.equal(true)
    sinon.restore()
  })

  it('bump is a no-op without a cache or an org', async () => {
    const logger = createMockLogger()
    await bumpTeamsVersion('o1', logger, undefined)
    const cache = new MemoryCache()
    await bumpTeamsVersion('', logger, cache)
    expect(cache.store.size).to.equal(0)
  })
})
