import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { CrawlingSchedulerService } from '../../../../src/modules/crawling_manager/services/crawling_service'
import { createFakeRedisProvider, makeFakeRedisClient, stubGetRedisProvider } from '../../../helpers/fake-redis-provider'

/** Schedule changes a failed save still owes, kept in Redis so a restart does not drop them. */
describe('CrawlingSchedulerService: schedule changes owed by failed saves', () => {
  let restore: () => void
  let hash: Map<string, string>
  let onHget: (() => void) | undefined
  let scheduler: CrawlingSchedulerService

  beforeEach(() => {
    hash = new Map()
    const client = () =>
      makeFakeRedisClient({
        hset: sinon.stub().callsFake(async (_key: string, field: string, value: string) => void hash.set(field, value)),
        hget: sinon.stub().callsFake(async (_key: string, field: string) => {
          const value = hash.get(field) ?? null
          onHget?.()
          return value
        }),
        hdel: sinon.stub().callsFake(async (_key: string, field: string) => Number(hash.delete(field))),
        hgetall: sinon.stub().callsFake(async () => Object.fromEntries(hash)),
        // The compare-and-delete script, as Redis runs it: atomically.
        eval: sinon.stub().callsFake(async (_script: string, _n: number, _key: string, field: string, expected: string) => {
          if (hash.get(field) !== expected) return 0
          hash.delete(field)
          return 1
        }),
      } as any)
    onHget = undefined
    restore = stubGetRedisProvider(createFakeRedisProvider(client))
    scheduler = new CrawlingSchedulerService({ host: 'localhost', port: 6379, db: 0 } as any)
  })

  afterEach(() => {
    restore()
    sinon.restore()
  })

  it('lists what was marked, as a new process would read it', async () => {
    await scheduler.markReconcilePending('conn-1', 'org-1')

    const owed = await scheduler.listReconcilesPending()

    expect(owed.map(({ connectorId, orgId }) => ({ connectorId, orgId }))).to.deep.equal([
      { connectorId: 'conn-1', orgId: 'org-1' },
    ])
  })

  it('clears a mark set before the change was applied', async () => {
    await scheduler.markReconcilePending('conn-1', 'org-1')

    await scheduler.clearReconcilePending('conn-1', Date.now())

    expect(await scheduler.listReconcilesPending()).to.deep.equal([])
  })

  it('keeps a mark a later save set while an older reconcile was running', async () => {
    const olderStartedAt = Date.now() - 60_000
    await scheduler.markReconcilePending('conn-1', 'org-1')

    await scheduler.clearReconcilePending('conn-1', olderStartedAt)

    expect(await scheduler.listReconcilesPending()).to.have.length(1)
  })

  it('keeps a mark a save wrote between the read and the delete', async () => {
    await scheduler.markReconcilePending('conn-1', 'org-1')
    onHget = () => hash.set('conn-1', JSON.stringify({ orgId: 'org-1', at: Date.now() + 1 }))

    await scheduler.clearReconcilePending('conn-1', Date.now() + 60_000)

    expect(await scheduler.listReconcilesPending()).to.have.length(1)
  })
})
