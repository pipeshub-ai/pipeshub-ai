import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { CrawlingScheduleType } from '../../../../../src/modules/crawling_manager/schema/enums'

describe('crawling_manager/schema/scheduler/scheduler', () => {
  afterEach(() => {
    sinon.restore()
  })

  let mod: typeof import('../../../../../src/modules/crawling_manager/schema/scheduler/scheduler')

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('../../../../../src/modules/crawling_manager/schema/scheduler/scheduler')
  })

  it('should be importable without errors', () => {
    expect(mod).to.exist
  })

  describe('ICustomScheduleConfig', () => {
    it('should allow creating conforming objects', () => {
      const config: import('../../../../../src/modules/crawling_manager/schema/scheduler/scheduler').ICustomScheduleConfig = {
        cronExpression: '0 * * * *',
        timezone: 'UTC',
        description: 'Every hour',
      }
      expect(config.cronExpression).to.equal('0 * * * *')
      expect(config.timezone).to.equal('UTC')
    })
  })

  describe('IWeeklyScheduleConfig', () => {
    it('should allow creating conforming objects', () => {
      const config: import('../../../../../src/modules/crawling_manager/schema/scheduler/scheduler').IWeeklyScheduleConfig = {
        daysOfWeek: [1, 3, 5],
        hour: 9,
        minute: 30,
        timezone: 'America/New_York',
      }
      expect(config.daysOfWeek).to.deep.equal([1, 3, 5])
      expect(config.hour).to.equal(9)
      expect(config.minute).to.equal(30)
    })
  })

  describe('IDailyScheduleConfig', () => {
    it('should allow creating conforming objects', () => {
      const config: import('../../../../../src/modules/crawling_manager/schema/scheduler/scheduler').IDailyScheduleConfig = {
        hour: 14,
        minute: 0,
      }
      expect(config.hour).to.equal(14)
      expect(config.minute).to.equal(0)
    })
  })

  describe('IHourlyScheduleConfig', () => {
    it('should allow creating conforming objects', () => {
      const config: import('../../../../../src/modules/crawling_manager/schema/scheduler/scheduler').IHourlyScheduleConfig = {
        minute: 15,
        interval: 2,
      }
      expect(config.minute).to.equal(15)
      expect(config.interval).to.equal(2)
    })
  })

  describe('IMonthlyScheduleConfig', () => {
    it('should allow creating conforming objects', () => {
      const config: import('../../../../../src/modules/crawling_manager/schema/scheduler/scheduler').IMonthlyScheduleConfig = {
        dayOfMonth: 15,
        hour: 3,
        minute: 0,
        timezone: 'UTC',
      }
      expect(config.dayOfMonth).to.equal(15)
    })
  })

  describe('IOnceScheduleConfig', () => {
    it('should allow creating conforming objects', () => {
      const scheduledTime = new Date('2026-12-01T00:00:00Z')
      const config: import('../../../../../src/modules/crawling_manager/schema/scheduler/scheduler').IOnceScheduleConfig = {
        scheduledTime,
        timezone: 'UTC',
      }
      expect(config.scheduledTime).to.equal(scheduledTime)
    })
  })

  describe('specific crawling schedule interfaces', () => {
    it('should allow creating ICustomCrawlingSchedule', () => {
      const schedule: import('../../../../../src/modules/crawling_manager/schema/scheduler/scheduler').ICustomCrawlingSchedule = {
        scheduleType: CrawlingScheduleType.CUSTOM,
        scheduleConfig: { cronExpression: '0 0 * * *' },
        isEnabled: true,
        createdBy: {} as any,
        lastUpdatedBy: {} as any,
      }
      expect(schedule.scheduleType).to.equal(CrawlingScheduleType.CUSTOM)
    })

    it('should allow creating IWeeklyCrawlingSchedule', () => {
      const schedule: import('../../../../../src/modules/crawling_manager/schema/scheduler/scheduler').IWeeklyCrawlingSchedule = {
        scheduleType: CrawlingScheduleType.WEEKLY,
        scheduleConfig: { daysOfWeek: [0, 6], hour: 8, minute: 0 },
        isEnabled: true,
        createdBy: {} as any,
        lastUpdatedBy: {} as any,
      }
      expect(schedule.scheduleType).to.equal(CrawlingScheduleType.WEEKLY)
    })

    it('should allow creating IDailyCrawlingSchedule', () => {
      const schedule: import('../../../../../src/modules/crawling_manager/schema/scheduler/scheduler').IDailyCrawlingSchedule = {
        scheduleType: CrawlingScheduleType.DAILY,
        scheduleConfig: { hour: 12, minute: 30 },
        isEnabled: true,
        createdBy: {} as any,
        lastUpdatedBy: {} as any,
      }
      expect(schedule.scheduleType).to.equal(CrawlingScheduleType.DAILY)
    })

    it('should allow creating IHourlyCrawlingSchedule', () => {
      const schedule: import('../../../../../src/modules/crawling_manager/schema/scheduler/scheduler').IHourlyCrawlingSchedule = {
        scheduleType: CrawlingScheduleType.HOURLY,
        scheduleConfig: { minute: 0 },
        isEnabled: false,
        createdBy: {} as any,
        lastUpdatedBy: {} as any,
      }
      expect(schedule.scheduleType).to.equal(CrawlingScheduleType.HOURLY)
      expect(schedule.isEnabled).to.be.false
    })

    it('should allow creating IMonthlyCrawlingSchedule', () => {
      const schedule: import('../../../../../src/modules/crawling_manager/schema/scheduler/scheduler').IMonthlyCrawlingSchedule = {
        scheduleType: CrawlingScheduleType.MONTHLY,
        scheduleConfig: { dayOfMonth: 1, hour: 0, minute: 0 },
        isEnabled: true,
        createdBy: {} as any,
        lastUpdatedBy: {} as any,
      }
      expect(schedule.scheduleType).to.equal(CrawlingScheduleType.MONTHLY)
    })

    it('should allow creating IOnceCrawlingSchedule', () => {
      const schedule: import('../../../../../src/modules/crawling_manager/schema/scheduler/scheduler').IOnceCrawlingSchedule = {
        scheduleType: CrawlingScheduleType.ONCE,
        scheduleConfig: { scheduledTime: new Date() },
        isEnabled: true,
        createdBy: {} as any,
        lastUpdatedBy: {} as any,
      }
      expect(schedule.scheduleType).to.equal(CrawlingScheduleType.ONCE)
    })
  })
})
