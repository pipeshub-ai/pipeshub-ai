import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  ConnectorTypeSchema,
  CrawlingScheduleRequestSchema,
  JobStatusRequestSchema,
} from '../../../../src/modules/crawling_manager/validator/validator'

describe('crawling_manager/validator/validator', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('ConnectorTypeSchema', () => {
    it('should accept valid connector params', () => {
      const data = { params: { connector: 'drive', connectorId: 'conn-123' } }
      const result = ConnectorTypeSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject missing connector', () => {
      const data = { params: { connectorId: 'conn-123' } }
      const result = ConnectorTypeSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject missing connectorId', () => {
      const data = { params: { connector: 'drive' } }
      const result = ConnectorTypeSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('CrawlingScheduleRequestSchema', () => {
    it('should accept valid daily schedule', () => {
      const data = {
        params: { connector: 'drive', connectorId: 'conn-123' },
        body: {
          scheduleConfig: {
            scheduleType: 'daily',
            isEnabled: true,
            timezone: 'UTC',
            hour: 10,
            minute: 30,
          },
          priority: 5,
          maxRetries: 3,
          timeout: 300000,
        },
        headers: { authorization: 'Bearer token' },
      }
      const result = CrawlingScheduleRequestSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept valid hourly schedule', () => {
      const data = {
        params: { connector: 'drive', connectorId: 'conn-123' },
        body: {
          scheduleConfig: {
            scheduleType: 'hourly',
            isEnabled: true,
            timezone: 'UTC',
            minute: 0,
            interval: 2,
          },
          priority: 5,
          maxRetries: 3,
          timeout: 300000,
        },
        headers: { authorization: 'Bearer token' },
      }
      const result = CrawlingScheduleRequestSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should accept valid weekly schedule', () => {
      const data = {
        params: { connector: 'drive', connectorId: 'conn-123' },
        body: {
          scheduleConfig: {
            scheduleType: 'weekly',
            isEnabled: true,
            timezone: 'UTC',
            daysOfWeek: [1, 3, 5],
            hour: 9,
            minute: 0,
          },
          priority: 5,
          maxRetries: 3,
          timeout: 300000,
        },
        headers: { authorization: 'Bearer token' },
      }
      const result = CrawlingScheduleRequestSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject invalid priority range', () => {
      const data = {
        params: { connector: 'drive', connectorId: 'conn-123' },
        body: {
          scheduleConfig: {
            scheduleType: 'daily',
            isEnabled: true,
            timezone: 'UTC',
            hour: 10,
            minute: 30,
          },
          priority: 11,
          maxRetries: 3,
          timeout: 300000,
        },
        headers: { authorization: 'Bearer token' },
      }
      const result = CrawlingScheduleRequestSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should accept valid interval schedule', () => {
      const data = {
        params: { connector: 'confluence', connectorId: 'conn-123' },
        body: {
          scheduleConfig: {
            scheduleType: 'interval',
            isEnabled: true,
            timezone: 'UTC',
            scheduleConfig: {
              intervalMinutes: 5,
            },
          },
          priority: 5,
          maxRetries: 3,
          timeout: 300000,
        },
        headers: { authorization: 'Bearer token' },
      }
      const result = CrawlingScheduleRequestSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject interval schedule with intervalMinutes = 0', () => {
      const data = {
        params: { connector: 'confluence', connectorId: 'conn-123' },
        body: {
          scheduleConfig: {
            scheduleType: 'interval',
            isEnabled: true,
            timezone: 'UTC',
            scheduleConfig: {
              intervalMinutes: 0,
            },
          },
          priority: 5,
          maxRetries: 3,
          timeout: 300000,
        },
        headers: { authorization: 'Bearer token' },
      }
      const result = CrawlingScheduleRequestSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject interval schedule with missing intervalMinutes', () => {
      const data = {
        params: { connector: 'confluence', connectorId: 'conn-123' },
        body: {
          scheduleConfig: {
            scheduleType: 'interval',
            isEnabled: true,
            timezone: 'UTC',
            scheduleConfig: {},
          },
          priority: 5,
          maxRetries: 3,
          timeout: 300000,
        },
        headers: { authorization: 'Bearer token' },
      }
      const result = CrawlingScheduleRequestSchema.safeParse(data)
      expect(result.success).to.be.false
    })

    it('should reject invalid cron expression', () => {
      const data = {
        params: { connector: 'drive', connectorId: 'conn-123' },
        body: {
          scheduleConfig: {
            scheduleType: 'custom',
            isEnabled: true,
            timezone: 'UTC',
            cronExpression: 'not a cron',
          },
          priority: 5,
          maxRetries: 3,
          timeout: 300000,
        },
        headers: { authorization: 'Bearer token' },
      }
      const result = CrawlingScheduleRequestSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })

  describe('JobStatusRequestSchema', () => {
    it('should accept valid job status request', () => {
      const data = {
        params: { connector: 'drive', connectorId: 'conn-123' },
        headers: { authorization: 'Bearer token' },
      }
      const result = JobStatusRequestSchema.safeParse(data)
      expect(result.success).to.be.true
    })

    it('should reject missing authorization', () => {
      const data = {
        params: { connector: 'drive', connectorId: 'conn-123' },
        headers: {},
      }
      const result = JobStatusRequestSchema.safeParse(data)
      expect(result.success).to.be.false
    })
  })
})
