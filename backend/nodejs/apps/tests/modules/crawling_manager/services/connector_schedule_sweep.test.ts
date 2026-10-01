import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ConnectorScheduleSweepService } from '../../../../src/modules/crawling_manager/services/connector_schedule_sweep'
import * as scheduledConnectorsClient from '../../../../src/modules/crawling_manager/utils/scheduled_connectors_client'
import { ScheduledConnectorRecord } from '../../../../src/modules/crawling_manager/utils/scheduled_connectors_client'
import { buildCrawlingScheduleFromSync } from '../../../../src/modules/crawling_manager/utils/schedule_config_mapper'
import { FakeQueueStore, REDIS_CONFIG, schedulerOver } from '../fake-crawling-queue'

const DAY = 1440

const listed = (items: ScheduledConnectorRecord[], partial = false) => ({
  items,
  partial,
})

const record = (
  connectorId: string,
  intervalMinutes: number | undefined = DAY,
  orgId = 'org-a',
): ScheduledConnectorRecord => ({
  connectorId,
  type: 'Web',
  orgId,
  ownerUserId: 'user-1',
  isActive: true,
  sync: { selectedStrategy: 'SCHEDULED', scheduledConfig: { intervalMinutes, timezone: 'UTC' } },
})

describe('ConnectorScheduleSweepService', () => {
  let store: FakeQueueStore
  let scheduler: ReturnType<typeof schedulerOver>['service']
  let sweeper: ConnectorScheduleSweepService
  let fetchAll: sinon.SinonStub

  const schedulesFor = (connectorId: string) => {
    const name = scheduler.jobNameFor('Web', connectorId)
    return [...store.repeatables.values()].filter((r) => r.name === name)
  }

  // Seeds BullMQ the way a successful in-request reconcile would have.
  const seed = async (...records: ScheduledConnectorRecord[]) => {
    fetchAll.resolves(listed(records))
    await sweeper.sweep()
  }

  beforeEach(() => {
    store = new FakeQueueStore()
    scheduler = schedulerOver(store).service
    sweeper = new ConnectorScheduleSweepService(scheduler, {} as any, REDIS_CONFIG as any)
    fetchAll = sinon.stub(scheduledConnectorsClient, 'fetchAllScheduledConnectors')
  })

  afterEach(() => {
    sinon.restore()
    delete process.env.CONNECTOR_SCHEDULE_SWEEP_INTERVAL_MINUTES
  })

  it('recreates a missing schedule for an active scheduled connector', async () => {
    fetchAll.resolves(listed([record('c1', DAY, 'org-b')]))

    const summary = await sweeper.sweep()

    expect(summary.repaired).to.equal(1)
    const [schedule] = schedulesFor('c1')
    expect(schedule.every).to.equal(String(DAY * 60_000))
    const run = store.pending().find((j) => j.opts.repeatJobKey === schedule.key)
    expect(run?.data.orgId).to.equal('org-b')
  })

  it('leaves a schedule that already matches alone', async () => {
    await seed(record('c1'))
    const upsert = sinon.spy(scheduler, 'upsertRepeatingSchedule')

    const summary = await sweeper.sweep()

    expect(upsert.called).to.be.false
    expect(summary.repaired).to.equal(0)
    expect(schedulesFor('c1')).to.have.length(1)
  })

  it('replaces a schedule whose interval no longer matches the config', async () => {
    await seed(record('c1', 5))
    fetchAll.resolves(listed([record('c1', DAY)]))

    const summary = await sweeper.sweep()

    expect(summary.driftFixed).to.equal(1)
    expect(schedulesFor('c1').map((r) => r.every)).to.deep.equal([String(DAY * 60_000)])
  })

  it('keeps a stale schedule when the replacement cannot be added', async () => {
    await seed(record('c1', 5))
    sinon.stub(scheduler, 'upsertRepeatingSchedule').rejects(new Error('redis hiccup'))
    fetchAll.resolves(listed([record('c1', DAY)]))

    const summary = await sweeper.sweep()

    expect(summary.errors).to.equal(1)
    expect(summary.driftFixed).to.equal(0)
    expect(schedulesFor('c1').map((r) => r.every)).to.deep.equal([String(5 * 60_000)])
  })

  it('removes a schedule whose connector is no longer active and scheduled', async () => {
    await seed(record('c1'), record('c2'))
    fetchAll.resolves(listed([record('c2')]))

    const summary = await sweeper.sweep()

    expect(summary.orphansRemoved).to.equal(1)
    expect(schedulesFor('c1')).to.be.empty
    expect(schedulesFor('c2')).to.have.length(1)
  })

  it('changes nothing when the connector list cannot be fetched', async () => {
    await seed(record('c1'))
    fetchAll.rejects(new Error('connector service down'))

    const summary = await sweeper.sweep()

    expect(summary.aborted).to.be.true
    expect(schedulesFor('c1')).to.have.length(1)
  })

  it('keeps existing schedules when the list comes back empty', async () => {
    await seed(record('c1'))
    fetchAll.resolves(listed([]))

    const summary = await sweeper.sweep()

    expect(summary.orphansRemoved).to.equal(0)
    expect(schedulesFor('c1')).to.have.length(1)
  })

  it('never removes a schedule created while the sweep was fetching', async () => {
    await seed(record('c1'))
    fetchAll.callsFake(async () => {
      const late = record('late')
      await scheduler.scheduleJob(
        late.type,
        late.connectorId,
        buildCrawlingScheduleFromSync(late.sync, 'user-1')!,
        late.orgId,
        'user-1',
      )
      return listed([record('c1')])
    })

    await sweeper.sweep()

    expect(schedulesFor('late')).to.have.length(1)
  })

  it('keeps going when one connector fails', async () => {
    const upsert = scheduler.upsertRepeatingSchedule.bind(scheduler)
    sinon.stub(scheduler, 'upsertRepeatingSchedule').callsFake(async (connector, connectorId, ...rest) => {
      if (connectorId === 'c1') throw new Error('redis hiccup')
      return upsert(connector, connectorId, ...rest)
    })
    fetchAll.resolves(listed([record('c1'), record('c2')]))

    const summary = await sweeper.sweep()

    expect(summary.errors).to.equal(1)
    expect(summary.repaired).to.equal(1)
    expect(schedulesFor('c2')).to.have.length(1)
  })

  it('keeps the existing schedule of a connector with an invalid interval', async () => {
    await seed(record('c1'))
    fetchAll.resolves(listed([record('c1', undefined)]))

    await sweeper.sweep()

    expect(schedulesFor('c1')).to.have.length(1)
  })

  it('repairs listed connectors but does not remove orphans when the list is partial', async () => {
    await seed(record('c1'), record('c2', 5))
    fetchAll.resolves(listed([record('c2', DAY)], true))

    const summary = await sweeper.sweep()

    expect(summary.driftFixed).to.equal(1)
    expect(summary.orphansRemoved).to.equal(0)
    expect(schedulesFor('c1')).to.have.length(1)
    expect(schedulesFor('c2').map((r) => r.every)).to.deep.equal([String(DAY * 60_000)])
  })

  it('leaves a cron schedule in place when removing interval orphans', async () => {
    await seed(record('c1'))
    store.repeatables.set('cron-key', {
      key: 'cron-key',
      name: scheduler.jobNameFor('Web', 'cron-1'),
      id: null,
      endDate: null,
      tz: 'UTC',
      pattern: '0 2 * * *',
      every: null,
      next: Date.now(),
    })
    fetchAll.resolves(listed([record('c1')]))

    const summary = await sweeper.sweep()

    expect(summary.orphansRemoved).to.equal(0)
    expect(store.repeatables.has('cron-key')).to.be.true
    expect(schedulesFor('c1')).to.have.length(1)
  })

  it('does not start when the interval is 0', async () => {
    process.env.CONNECTOR_SCHEDULE_SWEEP_INTERVAL_MINUTES = '0'

    await sweeper.start()

    expect((sweeper as any).queue).to.be.null
    expect((sweeper as any).worker).to.be.null
  })
})
