import 'reflect-metadata'
import { expect } from 'chai'
import { CrawlingScheduleType } from '../../../../src/modules/crawling_manager/schema/enums'
import { FakeQueueStore, schedulerOver } from '../fake-crawling-queue'

// The connector type arrives in the URL (`/crawlingManager/:connector/:connectorId/...`), so the
// same schedule can be asked for as `Jira` and as `jira`. The job id has always folded case; the
// lookups did not, so a schedule set under one spelling was invisible under the other.
describe('CrawlingSchedulerService: a connector type is one type however it is spelled', () => {
  const ORG = 'org-a'
  const USER = 'user-1'
  const CONNECTOR_ID = 'c-1'
  const daily = {
    scheduleType: CrawlingScheduleType.DAILY,
    isEnabled: true,
    hour: 3,
    minute: 0,
    timezone: 'UTC',
  } as never

  it('finds, pauses, resumes and removes a schedule under a different spelling', async () => {
    const store = new FakeQueueStore()
    const { service } = schedulerOver(store)

    await service.scheduleJob('jira', CONNECTOR_ID, daily, ORG, USER)
    expect(store.repeatables.size).to.equal(1)

    const status = await service.getJobStatus('Jira', CONNECTOR_ID, ORG)
    expect(status, 'the schedule set as `jira` is found as `Jira`').to.not.equal(null)

    await service.pauseJob('Jira', CONNECTOR_ID, ORG)
    expect((await service.getJobStatus('JIRA', CONNECTOR_ID, ORG))?.state).to.equal('paused')

    await service.resumeJob('jira', CONNECTOR_ID, ORG)
    expect(await service.getJobStatus('Jira', CONNECTOR_ID, ORG)).to.not.equal(null)

    await service.removeJob('JIRA', CONNECTOR_ID, ORG)
    expect(store.repeatables.size).to.equal(0)
    expect(store.pending()).to.have.length(0)
    expect(await service.getJobStatus('jira', CONNECTOR_ID, ORG)).to.equal(null)
  })

  it('still tells two connector types apart, and folds spaces as the job id does', async () => {
    const store = new FakeQueueStore()
    const { service } = schedulerOver(store)

    await service.scheduleJob('Google Drive', CONNECTOR_ID, daily, ORG, USER)
    expect(await service.getJobStatus('google drive', CONNECTOR_ID, ORG)).to.not.equal(null)
    expect(await service.getJobStatus('Jira', CONNECTOR_ID, ORG)).to.equal(null)
  })

  it('replaces, not duplicates, a schedule set again under another spelling', async () => {
    const store = new FakeQueueStore()
    const { service } = schedulerOver(store)

    await service.scheduleJob('jira', CONNECTOR_ID, daily, ORG, USER)
    // A different hour: the fake keys a schedule by its repeat options, so an old one left
    // behind would show up as a second entry.
    const later = { ...(daily as object), hour: 4 } as never
    await service.scheduleJob('Jira', CONNECTOR_ID, later, ORG, USER)
    const kept = [...store.repeatables.values()]
    expect(kept).to.have.length(1)
    expect(JSON.stringify(kept[0])).to.match(/\b0 4 \* \* \*/)
  })
})
