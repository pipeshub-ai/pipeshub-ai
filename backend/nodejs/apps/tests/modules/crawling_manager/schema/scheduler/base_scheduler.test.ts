import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('crawling_manager/schema/scheduler/base_scheduler', () => {
  afterEach(() => {
    sinon.restore()
  })

  let mod: typeof import('../../../../../src/modules/crawling_manager/schema/scheduler/base_scheduler')

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('../../../../../src/modules/crawling_manager/schema/scheduler/base_scheduler')
  })

  it('should be importable without errors', () => {
    expect(mod).to.exist
  })

  it('should allow creating objects conforming to IBaseCrawlingSchedule shape', () => {
    const schedule: import('../../../../../src/modules/crawling_manager/schema/scheduler/base_scheduler').IBaseCrawlingSchedule = {
      isEnabled: true,
      createdBy: {} as any,
      lastUpdatedBy: {} as any,
    }
    expect(schedule.isEnabled).to.be.true
    expect(schedule.createdBy).to.exist
    expect(schedule.lastUpdatedBy).to.exist
  })

  it('should allow optional fields', () => {
    const schedule: import('../../../../../src/modules/crawling_manager/schema/scheduler/base_scheduler').IBaseCrawlingSchedule = {
      isEnabled: false,
      nextRunTime: new Date(),
      lastRunTime: new Date(),
      createdBy: {} as any,
      lastUpdatedBy: {} as any,
      createdAt: new Date(),
      updatedAt: new Date(),
    }
    expect(schedule.nextRunTime).to.be.instanceOf(Date)
    expect(schedule.lastRunTime).to.be.instanceOf(Date)
    expect(schedule.createdAt).to.be.instanceOf(Date)
    expect(schedule.updatedAt).to.be.instanceOf(Date)
  })
})
