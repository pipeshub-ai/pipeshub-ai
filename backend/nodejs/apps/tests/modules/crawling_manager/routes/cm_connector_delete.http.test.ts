import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createConnectorRouter } from '../../../../src/modules/tokens_manager/routes/connectors.routes'
import { CrawlingSchedulerService } from '../../../../src/modules/crawling_manager/services/crawling_service'
import { CrawlingScheduleType } from '../../../../src/modules/crawling_manager/schema/enums'
import { ICrawlingSchedule } from '../../../../src/modules/crawling_manager/schema/interface'
import {
  ADMIN,
  Harness,
  MEMBER_ID,
  ORG_A,
  call,
  sessionToken,
  startHarness,
} from '../../tokens_manager/routes/connectors-http-harness'
import { FakeQueueStore, schedulerOver } from '../fake-crawling-queue'

const TYPE = 'Google Drive'
const EVERY_HOUR = {
  scheduleType: CrawlingScheduleType.INTERVAL,
  isEnabled: true,
  scheduleConfig: { intervalMinutes: 60, timezone: 'UTC' },
} as unknown as ICrawlingSchedule

const until = async (check: () => boolean, what: string): Promise<void> => {
  for (let i = 0; i < 100; i += 1) {
    if (check()) return
    await new Promise((resolve) => setTimeout(resolve, 20))
  }
  expect.fail(`timed out waiting until ${what}`)
}

describe('Deleting a connector clears its sync schedule', () => {
  let h: Harness
  let store: FakeQueueStore
  let scheduler: CrawlingSchedulerService

  beforeEach(async () => {
    store = new FakeQueueStore()
    ;({ service: scheduler } = schedulerOver(store))
    const crawlingContainer = new Container()
    crawlingContainer.bind(CrawlingSchedulerService).toConstantValue(scheduler)
    h = await startHarness({ createRouter: (container) => createConnectorRouter(container, crawlingContainer) })
    h.backend.on('GET', '/api/v1/connectors/drive-1/config', {
      status: 200,
      body: { success: true, config: { type: TYPE, isActive: true, createdBy: ADMIN._id, config: { sync: {} } } },
    })
    await scheduler.scheduleJob(TYPE, 'drive-1', EVERY_HOUR, ORG_A, ADMIN._id)
    await scheduler.scheduleJob(TYPE, 'drive-2', EVERY_HOUR, ORG_A, ADMIN._id)
  })

  afterEach(async () => {
    await h.close()
    sinon.restore()
  })

  const schedulesFor = (connectorId: string) =>
    [...store.jobs.values()].filter((j) => j.data.connectorId === connectorId && j.state === 'delayed')

  it('removes the schedule, and nothing is left to fire, once the connector is gone', async () => {
    h.backend.on('DELETE', '/api/v1/connectors/drive-1', { status: 200, body: { success: true } })

    const res = await call(h, 'DELETE', '/drive-1', sessionToken(h, ADMIN))
    expect(res.status).to.equal(200)

    await until(() => schedulesFor('drive-1').length === 0, 'the deleted connector has no pending run')
    expect(store.repeatables.size).to.equal(1)
    expect(schedulesFor('drive-2')).to.have.length(1)
  })

  it('clears a paused schedule too, so it cannot be resumed for a connector that no longer exists', async () => {
    await scheduler.pauseJob(TYPE, 'drive-1', ORG_A)
    h.backend.on('DELETE', '/api/v1/connectors/drive-1', { status: 200, body: { success: true } })

    expect((await call(h, 'DELETE', '/drive-1', sessionToken(h, ADMIN))).status).to.equal(200)

    await until(() => scheduler.getPausedJobs().size === 0, 'the paused schedule is forgotten')
    let resumeError: unknown
    await scheduler.resumeJob(TYPE, 'drive-1', ORG_A).catch((e: unknown) => {
      resumeError = e
    })
    expect(resumeError).to.be.instanceOf(Error)
    expect(schedulesFor('drive-1')).to.have.length(0)
  })

  it('keeps the schedule when the connector service refuses the deletion', async () => {
    h.backend.on('DELETE', '/api/v1/connectors/drive-1', { status: 403, body: { detail: 'Only the owner can delete this connector' } })

    const res = await call(h, 'DELETE', '/drive-1', sessionToken(h, ADMIN))
    expect(res.status).to.equal(403)

    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(schedulesFor('drive-1')).to.have.length(1)
    expect(store.repeatables.size).to.equal(2)
  })

  it('still removes the other schedules when one of them cannot be removed, and the delete still answers', async () => {
    await scheduler.scheduleJob('Slack', 'drive-1', EVERY_HOUR, ORG_A, ADMIN._id)
    const removeJob = scheduler.removeJob.bind(scheduler)
    const attempted: string[] = []
    sinon.stub(scheduler, 'removeJob').callsFake(async (connector: string, connectorId: string, orgId: string) => {
      attempted.push(connector)
      if (connector === TYPE) throw new Error('redis timed out')
      return removeJob(connector, connectorId, orgId)
    })
    h.backend.on('DELETE', '/api/v1/connectors/drive-1', { status: 202, body: { success: true } })

    expect((await call(h, 'DELETE', '/drive-1', sessionToken(h, ADMIN))).status).to.equal(202)

    await until(() => attempted.length === 2, 'both schedules were tried')
    await until(
      () => !schedulesFor('drive-1').some((j) => j.data.connector === 'Slack'),
      'the Slack schedule is removed',
    )
    expect(attempted[0]).to.equal(TYPE)
    expect(schedulesFor('drive-1').map((j) => j.data.connector)).to.deep.equal([TYPE])
  })

  describe("when an admin deletes a member's personal connector", () => {
    // The connector service shows a personal connector's config only to its
    // creator, so the admin's read of it is refused.
    beforeEach(async () => {
      h.backend.on('GET', '/api/v1/connectors/personal-1/config', {
        status: 404,
        body: { detail: 'This connector was not found.' },
      })
      h.backend.on('DELETE', '/api/v1/connectors/personal-1', {
        status: 202,
        body: { success: true, message: 'Connector deletion initiated', connectorId: 'personal-1', status: 'DELETING' },
      })
      await scheduler.scheduleJob(TYPE, 'personal-1', EVERY_HOUR, ORG_A, MEMBER_ID)
    })

    it('still removes its schedule, and leaves the other connectors alone', async () => {
      const res = await call(h, 'DELETE', '/personal-1', sessionToken(h, ADMIN))
      expect(res.status).to.equal(202)

      await until(() => schedulesFor('personal-1').length === 0, 'the deleted connector has no pending run')
      expect(store.repeatables.size).to.equal(2)
      expect(schedulesFor('drive-1')).to.have.length(1)
      expect(schedulesFor('drive-2')).to.have.length(1)
    })

    it('forgets its paused schedule too', async () => {
      await scheduler.pauseJob(TYPE, 'personal-1', ORG_A)

      expect((await call(h, 'DELETE', '/personal-1', sessionToken(h, ADMIN))).status).to.equal(202)

      await until(() => scheduler.getPausedJobs().size === 0, 'the paused schedule is forgotten')
      expect(schedulesFor('personal-1')).to.have.length(0)
    })
  })
})
