import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  isCustomCrawlingSchedule,
  isWeeklyCrawlingSchedule,
  isDailyCrawlingSchedule,
  isHourlyCrawlingSchedule,
  isMonthlyCrawlingSchedule,
  isOnceCrawlingSchedule,
} from '../../../../src/modules/crawling_manager/schema/interface'
import { CrawlingScheduleType } from '../../../../src/modules/crawling_manager/schema/enums'

describe('crawling_manager/schema/interface', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('type guard: isCustomCrawlingSchedule', () => {
    it('should return true for CUSTOM schedule type', () => {
      const schedule = { scheduleType: CrawlingScheduleType.CUSTOM } as any
      expect(isCustomCrawlingSchedule(schedule)).to.be.true
    })

    it('should return false for non-CUSTOM schedule type', () => {
      const schedule = { scheduleType: CrawlingScheduleType.DAILY } as any
      expect(isCustomCrawlingSchedule(schedule)).to.be.false
    })
  })

  describe('type guard: isWeeklyCrawlingSchedule', () => {
    it('should return true for WEEKLY schedule type', () => {
      const schedule = { scheduleType: CrawlingScheduleType.WEEKLY } as any
      expect(isWeeklyCrawlingSchedule(schedule)).to.be.true
    })

    it('should return false for non-WEEKLY schedule type', () => {
      const schedule = { scheduleType: CrawlingScheduleType.DAILY } as any
      expect(isWeeklyCrawlingSchedule(schedule)).to.be.false
    })
  })

  describe('type guard: isDailyCrawlingSchedule', () => {
    it('should return true for DAILY schedule type', () => {
      const schedule = { scheduleType: CrawlingScheduleType.DAILY } as any
      expect(isDailyCrawlingSchedule(schedule)).to.be.true
    })

    it('should return false for non-DAILY schedule type', () => {
      const schedule = { scheduleType: CrawlingScheduleType.HOURLY } as any
      expect(isDailyCrawlingSchedule(schedule)).to.be.false
    })
  })

  describe('type guard: isHourlyCrawlingSchedule', () => {
    it('should return true for HOURLY schedule type', () => {
      const schedule = { scheduleType: CrawlingScheduleType.HOURLY } as any
      expect(isHourlyCrawlingSchedule(schedule)).to.be.true
    })

    it('should return false for non-HOURLY schedule type', () => {
      const schedule = { scheduleType: CrawlingScheduleType.MONTHLY } as any
      expect(isHourlyCrawlingSchedule(schedule)).to.be.false
    })
  })

  describe('type guard: isMonthlyCrawlingSchedule', () => {
    it('should return true for MONTHLY schedule type', () => {
      const schedule = { scheduleType: CrawlingScheduleType.MONTHLY } as any
      expect(isMonthlyCrawlingSchedule(schedule)).to.be.true
    })

    it('should return false for non-MONTHLY schedule type', () => {
      const schedule = { scheduleType: CrawlingScheduleType.ONCE } as any
      expect(isMonthlyCrawlingSchedule(schedule)).to.be.false
    })
  })

  describe('type guard: isOnceCrawlingSchedule', () => {
    it('should return true for ONCE schedule type', () => {
      const schedule = { scheduleType: CrawlingScheduleType.ONCE } as any
      expect(isOnceCrawlingSchedule(schedule)).to.be.true
    })

    it('should return false for non-ONCE schedule type', () => {
      const schedule = { scheduleType: CrawlingScheduleType.CUSTOM } as any
      expect(isOnceCrawlingSchedule(schedule)).to.be.false
    })
  })

  describe('module exports', () => {
    it('should export all type guard functions', () => {
      expect(isCustomCrawlingSchedule).to.be.a('function')
      expect(isWeeklyCrawlingSchedule).to.be.a('function')
      expect(isDailyCrawlingSchedule).to.be.a('function')
      expect(isHourlyCrawlingSchedule).to.be.a('function')
      expect(isMonthlyCrawlingSchedule).to.be.a('function')
      expect(isOnceCrawlingSchedule).to.be.a('function')
    })
  })
})
