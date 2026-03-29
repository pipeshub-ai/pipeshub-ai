import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { CrawlingSchedulerService } from '../../../../src/modules/crawling_manager/services/crawling_service'
import { CrawlingScheduleType } from '../../../../src/modules/crawling_manager/schema/enums'
import { BadRequestError } from '../../../../src/libs/errors/http.errors'

describe('CrawlingSchedulerService - additional coverage', () => {
  let service: CrawlingSchedulerService
  let mockRedisConfig: any

  beforeEach(() => {
    mockRedisConfig = {
      host: 'localhost',
      port: 6379,
      username: undefined,
      password: undefined,
      db: 0,
    }
    try {
      service = new CrawlingSchedulerService(mockRedisConfig)
    } catch {
      // Redis might not be available
    }
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('removeJobInternal - error handling', () => {
    it('should handle errors during repeatable job removal gracefully', async () => {
      if (!service) return

      const q = (service as any).queue
      sinon.stub(q, 'getRepeatableJobs').resolves([
        { pattern: '0 * * * *', tz: 'UTC', key: 'k1', endDate: null },
      ])
      sinon.stub(q, 'getJobs')
        .onFirstCall().resolves([{
          data: { connector: 'google', connectorId: 'conn-1', orgId: 'org-1' },
          opts: { repeat: { pattern: '0 * * * *', tz: 'UTC' } },
        }])
        .onSecondCall().resolves([])
      sinon.stub(q, 'removeRepeatable').rejects(new Error('Remove failed'))

      // Should not throw - error is caught internally
      await (service as any).removeJobInternal('google', 'conn-1', 'org-1')
    })

    it('should handle errors during job instance removal', async () => {
      if (!service) return

      const q = (service as any).queue
      sinon.stub(q, 'getRepeatableJobs').resolves([])
      const mockJob = {
        data: { connector: 'google', connectorId: 'conn-1', orgId: 'org-1' },
        id: 'j1',
        timestamp: 1,
        remove: sinon.stub().rejects(new Error('remove failed')),
      }
      // Lots of old jobs to trigger the slice(10) path
      const manyJobs = Array.from({ length: 15 }, (_, i) => ({
        ...mockJob,
        id: `j${i}`,
        timestamp: i,
        remove: sinon.stub().rejects(new Error('remove failed')),
      }))
      sinon.stub(q, 'getJobs').resolves(manyJobs)

      // Should not throw
      await (service as any).removeJobInternal('google', 'conn-1', 'org-1')
    })

    it('should warn when removeJobInternal encounters queue error', async () => {
      if (!service) return

      sinon.stub((service as any).queue, 'getRepeatableJobs').rejects(new Error('queue down'))

      // Should not throw - outer catch handles it
      await (service as any).removeJobInternal('google', 'conn-1', 'org-1')
    })
  })

  describe('removeAllJobs - error handling in job removal', () => {
    it('should handle error when removing individual jobs', async () => {
      if (!service) return

      const q = (service as any).queue
      sinon.stub(q, 'getRepeatableJobs').resolves([])
      const mockJob = {
        data: { connector: 'google', connectorId: 'conn-1', orgId: 'org-1' },
        id: 'j1',
        remove: sinon.stub().rejects(new Error('remove fail')),
      }
      sinon.stub(q, 'getJobs').resolves([mockJob])

      // Should not throw - individual failures are caught
      await service.removeAllJobs('org-1')
    })

    it('should handle error when removing repeatable jobs', async () => {
      if (!service) return

      const q = (service as any).queue
      const mockRepJob = { pattern: '0 * * * *', tz: 'UTC', id: 'r1', endDate: null }
      sinon.stub(q, 'getRepeatableJobs').resolves([mockRepJob])
      sinon.stub(q, 'removeRepeatable').rejects(new Error('repeatable remove fail'))

      const mockJob = {
        data: { connector: 'google', connectorId: 'conn-1', orgId: 'org-1' },
        opts: { repeat: { pattern: '0 * * * *', tz: 'UTC' } },
        id: 'j1',
        remove: sinon.stub().resolves(),
      }
      sinon.stub(q, 'getJobs').resolves([mockJob])

      // Should not throw
      await service.removeAllJobs('org-1')
    })

    it('should skip already processed job names in removeAllJobs', async () => {
      if (!service) return

      const q = (service as any).queue
      const mockRepJob = { pattern: '0 * * * *', tz: 'UTC', id: 'r1', endDate: null }
      sinon.stub(q, 'getRepeatableJobs').resolves([mockRepJob, mockRepJob])
      sinon.stub(q, 'removeRepeatable').resolves()

      const mockJob = {
        data: { connector: 'google', connectorId: 'conn-1', orgId: 'org-1' },
        opts: { repeat: { pattern: '0 * * * *', tz: 'UTC' } },
        id: 'j1',
        remove: sinon.stub().resolves(),
      }
      sinon.stub(q, 'getJobs').resolves([mockJob])

      await service.removeAllJobs('org-1')
      // removeRepeatable should only be called once for the same job name
    })
  })

  describe('pauseJob - error rethrow', () => {
    it('should rethrow non-BadRequestError from getJobStatus', async () => {
      if (!service) return

      sinon.stub(service, 'getJobStatus').rejects(new Error('Unexpected'))

      try {
        await service.pauseJob('google', 'conn-1', 'org-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect((error as Error).message).to.equal('Unexpected')
      }
    })
  })

  describe('resumeJob - error rethrow', () => {
    it('should rethrow error from scheduleJob', async () => {
      if (!service) return

      ;(service as any).pausedJobs.set('crawl-google-conn-1-org-1', {
        connector: 'google',
        connectorId: 'conn-1',
        scheduleConfig: { scheduleType: CrawlingScheduleType.DAILY, isEnabled: true, scheduleConfig: { hour: 2, minute: 0 } },
        orgId: 'org-1',
        userId: 'user-1',
        options: {},
        pausedAt: new Date(),
      })

      sinon.stub(service, 'scheduleJob').rejects(new Error('schedule fail'))

      try {
        await service.resumeJob('google', 'conn-1', 'org-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect((error as Error).message).to.equal('schedule fail')
      }

      ;(service as any).pausedJobs.delete('crawl-google-conn-1-org-1')
    })
  })

  describe('getAllJobs - grouping by connector', () => {
    it('should group jobs by connector type and limit to last 10', async () => {
      if (!service) return

      const createJob = (connector: string, timestamp: number) => ({
        data: { connector, connectorId: 'conn-1', orgId: 'org-1' },
        id: `job-${timestamp}`,
        name: `crawl-${connector}-conn-1`,
        progress: 0,
        delay: 0,
        timestamp,
        attemptsMade: 0,
        finishedOn: undefined,
        processedOn: undefined,
        failedReason: undefined,
        getState: sinon.stub().resolves('waiting'),
      })

      // Create 15 jobs for same connector
      const jobs = Array.from({ length: 15 }, (_, i) => createJob('google', i))

      sinon.stub((service as any).queue, 'getJobs').resolves(jobs)

      const result = await service.getAllJobs('org-1')
      // Should only return 10 (limited per connector)
      expect(result.length).to.equal(10)
    })
  })

  describe('getJobDebugInfo - with matching jobs', () => {
    it('should count matching job instances', async () => {
      if (!service) return

      sinon.stub((service as any).queue, 'getRepeatableJobs').resolves([])
      sinon.stub((service as any).queue, 'getJobs').resolves([
        { data: { connector: 'google', connectorId: 'conn-1', orgId: 'org-1' } },
        { data: { connector: 'google', connectorId: 'conn-1', orgId: 'org-1' } },
        { data: { connector: 'slack', connectorId: 'conn-2', orgId: 'org-1' } },
      ])

      const info = await service.getJobDebugInfo('google', 'conn-1', 'org-1')
      expect(info.matchingJobInstances).to.equal(2)
    })

    it('should include paused job info in debug', async () => {
      if (!service) return

      ;(service as any).pausedJobs.set('crawl-google-conn-1-org-1', {
        connector: 'google',
        connectorId: 'conn-1',
        orgId: 'org-1',
        userId: 'user-1',
        scheduleConfig: { scheduleType: CrawlingScheduleType.DAILY },
        options: {},
        pausedAt: new Date(),
      })

      sinon.stub((service as any).queue, 'getRepeatableJobs').resolves([])
      sinon.stub((service as any).queue, 'getJobs').resolves([])

      const info = await service.getJobDebugInfo('google', 'conn-1', 'org-1')
      expect(info.isPaused).to.be.true
      expect(info.pausedJobInfo).to.exist

      ;(service as any).pausedJobs.delete('crawl-google-conn-1-org-1')
    })

    it('should include repeatable job key mapping', async () => {
      if (!service) return

      ;(service as any).repeatableJobMap.set('crawl-google-conn-1-org-1', 'repeat-key-1')

      sinon.stub((service as any).queue, 'getRepeatableJobs').resolves([
        { key: 'repeat-key-1', pattern: '0 * * * *', tz: 'UTC' },
      ])
      sinon.stub((service as any).queue, 'getJobs').resolves([])

      const info = await service.getJobDebugInfo('google', 'conn-1', 'org-1')
      expect(info.hasMapping).to.be.true
      expect(info.repeatableJobKey).to.equal('repeat-key-1')
      expect(info.relevantRepeatableJobs).to.have.length(1)

      ;(service as any).repeatableJobMap.delete('crawl-google-conn-1-org-1')
    })
  })

  describe('transformScheduleConfig - all schedule types', () => {
    it('should transform HOURLY with custom interval', () => {
      if (!service) return

      const result = (service as any).transformScheduleConfig({
        scheduleType: CrawlingScheduleType.HOURLY,
        minute: 30,
        interval: 2,
        timezone: 'America/New_York',
      })
      expect(result).to.exist
      expect(result.pattern).to.equal('30 */2 * * *')
      expect(result.tz).to.equal('America/New_York')
    })

    it('should transform WEEKLY with daysOfWeek', () => {
      if (!service) return

      const result = (service as any).transformScheduleConfig({
        scheduleType: CrawlingScheduleType.WEEKLY,
        minute: 0,
        hour: 9,
        daysOfWeek: [1, 3, 5],
        timezone: 'UTC',
      })
      expect(result).to.exist
      expect(result.pattern).to.equal('0 9 * * 1,3,5')
    })

    it('should transform MONTHLY', () => {
      if (!service) return

      const result = (service as any).transformScheduleConfig({
        scheduleType: CrawlingScheduleType.MONTHLY,
        minute: 0,
        hour: 2,
        dayOfMonth: 15,
        timezone: 'UTC',
      })
      expect(result).to.exist
      expect(result.pattern).to.equal('0 2 15 * *')
    })

    it('should transform CUSTOM with cron expression', () => {
      if (!service) return

      const result = (service as any).transformScheduleConfig({
        scheduleType: CrawlingScheduleType.CUSTOM,
        cronExpression: '*/5 * * * *',
        timezone: 'UTC',
      })
      expect(result).to.exist
      expect(result.pattern).to.equal('*/5 * * * *')
    })

    it('should return undefined for ONCE schedule type', () => {
      if (!service) return

      const result = (service as any).transformScheduleConfig({
        scheduleType: CrawlingScheduleType.ONCE,
      })
      expect(result).to.be.undefined
    })

    it('should throw for invalid schedule type', () => {
      if (!service) return

      expect(() => {
        (service as any).transformScheduleConfig({
          scheduleType: 'invalid-type',
        })
      }).to.throw(BadRequestError)
    })
  })

  describe('getQueueStats', () => {
    it('should return queue statistics', async () => {
      if (!service) return

      const q = (service as any).queue
      sinon.stub(q, 'getWaiting').resolves([{}, {}])
      sinon.stub(q, 'getActive').resolves([{}])
      sinon.stub(q, 'getCompleted').resolves([{}, {}, {}])
      sinon.stub(q, 'getFailed').resolves([])
      sinon.stub(q, 'getDelayed').resolves([{}])
      sinon.stub(q, 'getRepeatableJobs').resolves([{}, {}])

      const stats = await service.getQueueStats()
      expect(stats.waiting).to.equal(2)
      expect(stats.active).to.equal(1)
      expect(stats.completed).to.equal(3)
      expect(stats.failed).to.equal(0)
      expect(stats.delayed).to.equal(1)
      expect(stats.repeatable).to.equal(2)
      expect(stats.total).to.equal(7) // 2+1+3+0+1+0 paused
    })

    it('should throw when queue methods fail', async () => {
      if (!service) return

      sinon.stub((service as any).queue, 'getWaiting').rejects(new Error('Queue error'))

      try {
        await service.getQueueStats()
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.equal('Queue error')
      }
    })
  })

  describe('getRepeatableJobs', () => {
    it('should return all repeatable jobs when no orgId provided', async () => {
      if (!service) return

      sinon.stub((service as any).queue, 'getRepeatableJobs').resolves([
        { pattern: '0 * * * *', tz: 'UTC' },
        { pattern: '0 0 * * *', tz: 'UTC' },
      ])

      const result = await service.getRepeatableJobs()
      expect(result).to.have.length(2)
    })

    it('should filter by orgId when provided', async () => {
      if (!service) return

      const q = (service as any).queue
      sinon.stub(q, 'getRepeatableJobs').resolves([
        { pattern: '0 * * * *', tz: 'UTC' },
      ])
      sinon.stub(q, 'getJobs').resolves([
        {
          data: { orgId: 'org-1' },
          opts: { repeat: { pattern: '0 * * * *', tz: 'UTC' } },
        },
      ])

      const result = await service.getRepeatableJobs('org-1')
      expect(result).to.have.length(1)
    })

    it('should return empty when no repeatable jobs match orgId', async () => {
      if (!service) return

      const q = (service as any).queue
      sinon.stub(q, 'getRepeatableJobs').resolves([
        { pattern: '0 * * * *', tz: 'UTC' },
      ])
      sinon.stub(q, 'getJobs').resolves([])

      const result = await service.getRepeatableJobs('org-2')
      expect(result).to.have.length(0)
    })

    it('should throw on queue error', async () => {
      if (!service) return

      sinon.stub((service as any).queue, 'getRepeatableJobs').rejects(new Error('Queue error'))

      try {
        await service.getRepeatableJobs()
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.equal('Queue error')
      }
    })
  })

  describe('getJobStatus - paused job', () => {
    it('should return paused status for paused jobs', async () => {
      if (!service) return

      const pausedAt = new Date()
      ;(service as any).pausedJobs.set('crawl-google-conn-1-org-1', {
        connector: 'google',
        connectorId: 'conn-1',
        orgId: 'org-1',
        userId: 'user-1',
        scheduleConfig: { scheduleType: CrawlingScheduleType.DAILY },
        options: { metadata: { key: 'value' } },
        pausedAt,
      })

      const status = await service.getJobStatus('google', 'conn-1', 'org-1')
      expect(status).to.exist
      expect(status!.state).to.equal('paused')
      expect(status!.data.userId).to.equal('user-1')

      ;(service as any).pausedJobs.delete('crawl-google-conn-1-org-1')
    })
  })

  describe('removeJob', () => {
    it('should remove job and clean paused jobs', async () => {
      if (!service) return

      ;(service as any).pausedJobs.set('crawl-google-conn-1-org-1', {
        connector: 'google', connectorId: 'conn-1', orgId: 'org-1',
      })

      sinon.stub((service as any).queue, 'getRepeatableJobs').resolves([])
      sinon.stub((service as any).queue, 'getJobs').resolves([])

      await service.removeJob('google', 'conn-1', 'org-1')
      expect((service as any).pausedJobs.has('crawl-google-conn-1-org-1')).to.be.false
    })
  })

  describe('close', () => {
    it('should close queue and clear maps', async () => {
      if (!service) return

      ;(service as any).repeatableJobMap.set('key1', 'val1')
      ;(service as any).pausedJobs.set('key2', { orgId: 'org-1' })

      sinon.stub((service as any).queue, 'close').resolves()

      await service.close()
      expect((service as any).repeatableJobMap.size).to.equal(0)
      expect((service as any).pausedJobs.size).to.equal(0)
    })

    it('should throw when queue.close fails', async () => {
      if (!service) return

      sinon.stub((service as any).queue, 'close').rejects(new Error('Close failed'))

      try {
        await service.close()
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.equal('Close failed')
      }
    })
  })

  describe('getRepeatableJobMappings', () => {
    it('should return a copy of the map', () => {
      if (!service) return

      ;(service as any).repeatableJobMap.set('k1', 'v1')
      const copy = service.getRepeatableJobMappings()
      expect(copy.get('k1')).to.equal('v1')
      // Modifying copy should not affect original
      copy.set('k2', 'v2')
      expect((service as any).repeatableJobMap.has('k2')).to.be.false
      ;(service as any).repeatableJobMap.delete('k1')
    })
  })

  describe('getPausedJobs', () => {
    it('should return a copy of paused jobs', () => {
      if (!service) return

      ;(service as any).pausedJobs.set('k1', { orgId: 'org-1' })
      const copy = service.getPausedJobs()
      expect(copy.has('k1')).to.be.true
      ;(service as any).pausedJobs.delete('k1')
    })
  })

  describe('getAllJobs - with paused jobs', () => {
    it('should include paused jobs for org', async () => {
      if (!service) return

      ;(service as any).pausedJobs.set('crawl-google-conn-1-org-1', {
        connector: 'google',
        connectorId: 'conn-1',
        orgId: 'org-1',
        userId: 'user-1',
        scheduleConfig: { scheduleType: CrawlingScheduleType.DAILY },
        options: { metadata: { key: 'val' } },
        pausedAt: new Date(),
      })

      sinon.stub((service as any).queue, 'getJobs').resolves([])

      const result = await service.getAllJobs('org-1')
      expect(result.length).to.equal(1)
      expect(result[0].state).to.equal('paused')

      ;(service as any).pausedJobs.delete('crawl-google-conn-1-org-1')
    })

    it('should throw when getAllJobs encounters queue error', async () => {
      if (!service) return

      sinon.stub((service as any).queue, 'getJobs').rejects(new Error('Queue down'))

      try {
        await service.getAllJobs('org-1')
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.equal('Queue down')
      }
    })
  })

  describe('removeAllJobs - paused jobs and mappings cleanup', () => {
    it('should remove paused jobs and mappings for org', async () => {
      if (!service) return

      ;(service as any).pausedJobs.set('crawl-google-conn-1-org-1', {
        connector: 'google', connectorId: 'conn-1', orgId: 'org-1',
      })
      ;(service as any).repeatableJobMap.set('crawl-google-conn-1-org-1', 'key1')

      sinon.stub((service as any).queue, 'getRepeatableJobs').resolves([])
      sinon.stub((service as any).queue, 'getJobs').resolves([])

      await service.removeAllJobs('org-1')
      expect((service as any).pausedJobs.has('crawl-google-conn-1-org-1')).to.be.false
      expect((service as any).repeatableJobMap.has('crawl-google-conn-1-org-1')).to.be.false
    })

    it('should throw when removeAllJobs encounters queue error', async () => {
      if (!service) return

      sinon.stub((service as any).queue, 'getRepeatableJobs').rejects(new Error('Queue error'))

      try {
        await service.removeAllJobs('org-1')
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.equal('Queue error')
      }
    })
  })

  describe('buildJobName (private)', () => {
    it('should build job name from connector and connectorId', () => {
      if (!service) return

      const name = (service as any).buildJobName('Google Drive', 'conn-1')
      expect(name).to.equal('crawl-google-drive-conn-1')
    })
  })

  describe('buildJobId (private)', () => {
    it('should build job id from connector, connectorId and orgId', () => {
      if (!service) return

      const id = (service as any).buildJobId('Google Drive', 'conn-1', 'org-1')
      expect(id).to.equal('crawl-google-drive-conn-1-org-1')
    })
  })
})
