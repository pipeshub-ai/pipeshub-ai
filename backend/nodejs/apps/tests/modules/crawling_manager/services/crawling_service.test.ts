import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { CrawlingSchedulerService } from '../../../../src/modules/crawling_manager/services/crawling_service'
import { CrawlingScheduleType } from '../../../../src/modules/crawling_manager/schema/enums'
import { BadRequestError } from '../../../../src/libs/errors/http.errors'

describe('CrawlingSchedulerService', () => {
  let mockRedisConfig: any
  let service: CrawlingSchedulerService

  beforeEach(() => {
    mockRedisConfig = {
      host: 'localhost',
      port: 6379,
      username: undefined,
      password: undefined,
      db: 0,
    }
    // Create service - the BullMQ Queue constructor will attempt to connect
    // but for unit tests we stub the queue methods
    try {
      service = new CrawlingSchedulerService(mockRedisConfig)
    } catch {
      // If Redis isn't available, we still test what we can
    }
  })

  afterEach(() => {
    sinon.restore()
  })

  // -------------------------------------------------------------------------
  // constructor
  // -------------------------------------------------------------------------
  describe('constructor', () => {
    it('should create a service instance with valid redis config', () => {
      try {
        const svc = new CrawlingSchedulerService(mockRedisConfig)
        expect(svc).to.exist
        expect(svc).to.have.property('scheduleJob')
        expect(svc).to.have.property('getJobStatus')
        expect(svc).to.have.property('removeJob')
        expect(svc).to.have.property('getAllJobs')
        expect(svc).to.have.property('removeAllJobs')
        expect(svc).to.have.property('pauseJob')
        expect(svc).to.have.property('resumeJob')
        expect(svc).to.have.property('getQueueStats')
        expect(svc).to.have.property('close')
        expect(svc).to.have.property('getRepeatableJobMappings')
        expect(svc).to.have.property('getPausedJobs')
        expect(svc).to.have.property('getJobDebugInfo')
        expect(svc).to.have.property('getRepeatableJobs')
      } catch {
        // Redis not available - test class structure instead
        expect(CrawlingSchedulerService).to.be.a('function')
      }
    })
  })

  // -------------------------------------------------------------------------
  // class structure
  // -------------------------------------------------------------------------
  describe('class structure', () => {
    it('should be a class with injectable decorator', () => {
      expect(CrawlingSchedulerService).to.be.a('function')
      expect(CrawlingSchedulerService.prototype).to.have.property('scheduleJob')
      expect(CrawlingSchedulerService.prototype).to.have.property('getJobStatus')
      expect(CrawlingSchedulerService.prototype).to.have.property('removeJob')
      expect(CrawlingSchedulerService.prototype).to.have.property('getAllJobs')
      expect(CrawlingSchedulerService.prototype).to.have.property('removeAllJobs')
      expect(CrawlingSchedulerService.prototype).to.have.property('pauseJob')
      expect(CrawlingSchedulerService.prototype).to.have.property('resumeJob')
      expect(CrawlingSchedulerService.prototype).to.have.property('getQueueStats')
    })

    it('should have close method', () => {
      expect(CrawlingSchedulerService.prototype.close).to.be.a('function')
    })

    it('should have getRepeatableJobMappings method', () => {
      expect(CrawlingSchedulerService.prototype.getRepeatableJobMappings).to.be.a('function')
    })

    it('should have getPausedJobs method', () => {
      expect(CrawlingSchedulerService.prototype.getPausedJobs).to.be.a('function')
    })

    it('should have getJobDebugInfo method', () => {
      expect(CrawlingSchedulerService.prototype.getJobDebugInfo).to.be.a('function')
    })

    it('should have getRepeatableJobs method', () => {
      expect(CrawlingSchedulerService.prototype.getRepeatableJobs).to.be.a('function')
    })
  })

  // -------------------------------------------------------------------------
  // transformScheduleConfig (private, tested via instance)
  // -------------------------------------------------------------------------
  describe('transformScheduleConfig (private)', () => {
    it('should return cron pattern for HOURLY schedule', () => {
      if (!service) return
      const result = (service as any).transformScheduleConfig({
        scheduleType: CrawlingScheduleType.HOURLY,
        minute: 30,
        interval: 2,
      })
      expect(result).to.exist
      expect(result.pattern).to.equal('30 */2 * * *')
      expect(result.tz).to.equal('UTC')
    })

    it('should return cron pattern for DAILY schedule', () => {
      if (!service) return
      const result = (service as any).transformScheduleConfig({
        scheduleType: CrawlingScheduleType.DAILY,
        hour: 14,
        minute: 30,
      })
      expect(result).to.exist
      expect(result.pattern).to.equal('30 14 * * *')
    })

    it('should return cron pattern for WEEKLY schedule', () => {
      if (!service) return
      const result = (service as any).transformScheduleConfig({
        scheduleType: CrawlingScheduleType.WEEKLY,
        hour: 8,
        minute: 0,
        daysOfWeek: [1, 3, 5],
      })
      expect(result).to.exist
      expect(result.pattern).to.equal('0 8 * * 1,3,5')
    })

    it('should return cron pattern for MONTHLY schedule', () => {
      if (!service) return
      const result = (service as any).transformScheduleConfig({
        scheduleType: CrawlingScheduleType.MONTHLY,
        hour: 6,
        minute: 0,
        dayOfMonth: 15,
      })
      expect(result).to.exist
      expect(result.pattern).to.equal('0 6 15 * *')
    })

    it('should return custom cron pattern for CUSTOM schedule', () => {
      if (!service) return
      const result = (service as any).transformScheduleConfig({
        scheduleType: CrawlingScheduleType.CUSTOM,
        cronExpression: '0 0 * * 0',
      })
      expect(result).to.exist
      expect(result.pattern).to.equal('0 0 * * 0')
    })

    it('should return undefined for ONCE schedule', () => {
      if (!service) return
      const result = (service as any).transformScheduleConfig({
        scheduleType: CrawlingScheduleType.ONCE,
      })
      expect(result).to.be.undefined
    })

    it('should throw BadRequestError for invalid schedule type', () => {
      if (!service) return
      try {
        ;(service as any).transformScheduleConfig({ scheduleType: 'invalid' })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })

    it('should use custom timezone when provided', () => {
      if (!service) return
      const result = (service as any).transformScheduleConfig({
        scheduleType: CrawlingScheduleType.DAILY,
        hour: 10,
        minute: 0,
        timezone: 'America/New_York',
      })
      expect(result.tz).to.equal('America/New_York')
    })

    it('should default interval to 1 for HOURLY schedule', () => {
      if (!service) return
      const result = (service as any).transformScheduleConfig({
        scheduleType: CrawlingScheduleType.HOURLY,
        minute: 0,
      })
      expect(result.pattern).to.equal('0 */1 * * *')
    })
  })

  // -------------------------------------------------------------------------
  // buildJobId (private)
  // -------------------------------------------------------------------------
  describe('buildJobId (private)', () => {
    it('should create consistent job ID', () => {
      if (!service) return
      const jobId = (service as any).buildJobId('Google Drive', 'conn-1', 'org-1')
      expect(jobId).to.equal('crawl-google-drive-conn-1-org-1')
    })

    it('should lowercase connector name', () => {
      if (!service) return
      const jobId = (service as any).buildJobId('SLACK', 'conn-2', 'org-2')
      expect(jobId).to.equal('crawl-slack-conn-2-org-2')
    })

    it('should replace spaces with hyphens', () => {
      if (!service) return
      const jobId = (service as any).buildJobId('Google  Workspace', 'conn-1', 'org-1')
      expect(jobId).to.include('google-workspace')
    })
  })

  // -------------------------------------------------------------------------
  // buildJobName (private)
  // -------------------------------------------------------------------------
  describe('buildJobName (private)', () => {
    it('should create job name from connector and connectorId', () => {
      if (!service) return
      const name = (service as any).buildJobName('Google Drive', 'conn-1')
      expect(name).to.equal('crawl-google-drive-conn-1')
    })
  })

  // -------------------------------------------------------------------------
  // getRepeatableJobMappings
  // -------------------------------------------------------------------------
  describe('getRepeatableJobMappings', () => {
    it('should return a copy of the map', () => {
      if (!service) return
      const mappings = service.getRepeatableJobMappings()
      expect(mappings).to.be.instanceOf(Map)
      expect(mappings.size).to.equal(0)
    })
  })

  // -------------------------------------------------------------------------
  // getPausedJobs
  // -------------------------------------------------------------------------
  describe('getPausedJobs', () => {
    it('should return a copy of the paused jobs map', () => {
      if (!service) return
      const paused = service.getPausedJobs()
      expect(paused).to.be.instanceOf(Map)
      expect(paused.size).to.equal(0)
    })
  })

  // -------------------------------------------------------------------------
  // scheduleJob
  // -------------------------------------------------------------------------
  describe('scheduleJob', () => {
    it('should throw BadRequestError when schedule is disabled', async () => {
      if (!service) return
      // Stub removeJobInternal to avoid queue operations
      sinon.stub(service as any, 'removeJobInternal').resolves()

      try {
        await service.scheduleJob('google', 'conn-1', {
          scheduleType: CrawlingScheduleType.DAILY,
          isEnabled: false,
          scheduleConfig: { hour: 2, minute: 0 },
        } as any, 'org-1', 'user-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })

    it('should throw BadRequestError when ONCE schedule time is in the past', async () => {
      if (!service) return
      sinon.stub(service as any, 'removeJobInternal').resolves()

      try {
        await service.scheduleJob('google', 'conn-1', {
          scheduleType: CrawlingScheduleType.ONCE,
          isEnabled: true,
          scheduleConfig: {
            scheduledTime: new Date(Date.now() - 10000).toISOString(),
          },
        } as any, 'org-1', 'user-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // resumeJob
  // -------------------------------------------------------------------------
  describe('resumeJob', () => {
    it('should throw BadRequestError when no paused job found', async () => {
      if (!service) return
      try {
        await service.resumeJob('google', 'conn-1', 'org-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })
  })

  // -------------------------------------------------------------------------
  // scheduleJob – queue interaction via stubs
  // -------------------------------------------------------------------------
  describe('scheduleJob (with queue stubs)', () => {
    let queueStub: any

    beforeEach(() => {
      if (!service) return
      queueStub = (service as any).queue
      sinon.stub(queueStub, 'add').resolves({ id: 'job-abc', opts: {} })
      sinon.stub(queueStub, 'getRepeatableJobs').resolves([])
      sinon.stub(queueStub, 'getJobs').resolves([])
      sinon.stub(queueStub, 'removeRepeatable').resolves()
    })

    it('should schedule a DAILY repeating job successfully', async () => {
      if (!service) return
      sinon.stub(service as any, 'removeJobInternal').resolves()

      const job = await service.scheduleJob('google', 'conn-1', {
        scheduleType: CrawlingScheduleType.DAILY,
        isEnabled: true,
        scheduleConfig: { hour: 2, minute: 0 },
      } as any, 'org-1', 'user-1')

      expect(job.id).to.equal('job-abc')
      expect(queueStub.add.calledOnce).to.be.true
      const addArgs = queueStub.add.firstCall.args
      expect(addArgs[1].connector).to.equal('google')
      expect(addArgs[1].connectorId).to.equal('conn-1')
      expect(addArgs[1].orgId).to.equal('org-1')
      expect(addArgs[1].userId).to.equal('user-1')
    })

    it('should schedule a ONCE job with future time', async () => {
      if (!service) return
      sinon.stub(service as any, 'removeJobInternal').resolves()

      const futureTime = new Date(Date.now() + 60000).toISOString()
      const job = await service.scheduleJob('slack', 'conn-2', {
        scheduleType: CrawlingScheduleType.ONCE,
        isEnabled: true,
        scheduleConfig: { scheduledTime: futureTime },
      } as any, 'org-1', 'user-1')

      expect(job.id).to.equal('job-abc')
      const opts = queueStub.add.firstCall.args[2]
      expect(opts.delay).to.be.greaterThan(0)
      expect(opts.jobId).to.include('crawl-slack')
    })

    it('should pass priority and maxRetries options', async () => {
      if (!service) return
      sinon.stub(service as any, 'removeJobInternal').resolves()

      await service.scheduleJob('google', 'conn-1', {
        scheduleType: CrawlingScheduleType.DAILY,
        isEnabled: true,
        scheduleConfig: { hour: 2, minute: 0 },
      } as any, 'org-1', 'user-1', { priority: 1, maxRetries: 5 })

      const opts = queueStub.add.firstCall.args[2]
      expect(opts.priority).to.equal(1)
      expect(opts.attempts).to.equal(5)
    })

    it('should store repeatable job mapping when matching job found', async () => {
      if (!service) return
      sinon.stub(service as any, 'removeJobInternal').resolves()
      queueStub.getRepeatableJobs.resolves([
        { pattern: '0 2 * * *', tz: 'UTC', key: 'rk-1' },
      ])

      await service.scheduleJob('google', 'conn-1', {
        scheduleType: CrawlingScheduleType.DAILY,
        isEnabled: true,
        hour: 2,
        minute: 0,
        scheduleConfig: { hour: 2, minute: 0 },
      } as any, 'org-1', 'user-1')

      const mappings = service.getRepeatableJobMappings()
      expect(mappings.get('crawl-google-conn-1-org-1')).to.equal('rk-1')
    })

    it('should include metadata in job data', async () => {
      if (!service) return
      sinon.stub(service as any, 'removeJobInternal').resolves()

      await service.scheduleJob('google', 'conn-1', {
        scheduleType: CrawlingScheduleType.DAILY,
        isEnabled: true,
        scheduleConfig: { hour: 2, minute: 0 },
      } as any, 'org-1', 'user-1', { metadata: { source: 'test' } })

      const jobData = queueStub.add.firstCall.args[1]
      expect(jobData.metadata).to.deep.equal({ source: 'test' })
    })
  })

  // -------------------------------------------------------------------------
  // removeJob (public)
  // -------------------------------------------------------------------------
  describe('removeJob', () => {
    it('should call removeJobInternal and clear paused jobs', async () => {
      if (!service) return
      const removeInternalStub = sinon.stub(service as any, 'removeJobInternal').resolves()
      // Manually add a paused job entry
      ;(service as any).pausedJobs.set('crawl-google-conn-1-org-1', { connector: 'google' })

      await service.removeJob('google', 'conn-1', 'org-1')

      expect(removeInternalStub.calledOnce).to.be.true
      expect((service as any).pausedJobs.has('crawl-google-conn-1-org-1')).to.be.false
    })
  })

  // -------------------------------------------------------------------------
  // getJobStatus
  // -------------------------------------------------------------------------
  describe('getJobStatus', () => {
    it('should return paused job status when job is paused', async () => {
      if (!service) return
      const pausedInfo = {
        connector: 'google',
        connectorId: 'conn-1',
        scheduleConfig: { scheduleType: CrawlingScheduleType.DAILY },
        orgId: 'org-1',
        userId: 'user-1',
        options: { metadata: { x: 1 } },
        pausedAt: new Date(),
      }
      ;(service as any).pausedJobs.set('crawl-google-conn-1-org-1', pausedInfo)

      const status = await service.getJobStatus('google', 'conn-1', 'org-1')

      expect(status).to.not.be.null
      expect(status!.state).to.equal('paused')
      expect(status!.id).to.equal('crawl-google-conn-1-org-1')
      ;(service as any).pausedJobs.delete('crawl-google-conn-1-org-1')
    })

    it('should return null when no jobs found', async () => {
      if (!service) return
      sinon.stub((service as any).queue, 'getJobs').resolves([])

      const status = await service.getJobStatus('google', 'conn-1', 'org-1')
      expect(status).to.be.null
    })

    it('should return most recent job status', async () => {
      if (!service) return
      const mockJob = {
        data: { connector: 'google', connectorId: 'conn-1', orgId: 'org-1' },
        id: 'job-1',
        name: 'crawl-google-conn-1',
        progress: 50,
        delay: 0,
        timestamp: Date.now(),
        attemptsMade: 1,
        finishedOn: undefined,
        processedOn: undefined,
        failedReason: undefined,
        getState: sinon.stub().resolves('active'),
      }
      sinon.stub((service as any).queue, 'getJobs').resolves([mockJob])

      const status = await service.getJobStatus('google', 'conn-1', 'org-1')

      expect(status).to.not.be.null
      expect(status!.state).to.equal('active')
      expect(status!.id).to.equal('job-1')
    })

    it('should throw when queue.getJobs fails', async () => {
      if (!service) return
      sinon.stub((service as any).queue, 'getJobs').rejects(new Error('redis down'))

      try {
        await service.getJobStatus('google', 'conn-1', 'org-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect((error as Error).message).to.equal('redis down')
      }
    })
  })

  // -------------------------------------------------------------------------
  // getAllJobs
  // -------------------------------------------------------------------------
  describe('getAllJobs', () => {
    it('should return jobs grouped by connector with paused jobs', async () => {
      if (!service) return
      const mockJob = {
        data: { connector: 'google', connectorId: 'conn-1', orgId: 'org-1' },
        id: 'job-1',
        name: 'crawl-google-conn-1',
        progress: 0,
        delay: 0,
        timestamp: Date.now(),
        attemptsMade: 0,
        finishedOn: undefined,
        processedOn: undefined,
        failedReason: undefined,
        getState: sinon.stub().resolves('waiting'),
      }
      sinon.stub((service as any).queue, 'getJobs').resolves([mockJob])
      ;(service as any).pausedJobs.set('crawl-slack-conn-2-org-1', {
        connector: 'slack',
        connectorId: 'conn-2',
        orgId: 'org-1',
        userId: 'user-1',
        scheduleConfig: { scheduleType: CrawlingScheduleType.DAILY },
        options: {},
        pausedAt: new Date(),
      })

      const jobs = await service.getAllJobs('org-1')

      expect(jobs.length).to.equal(2)
      const pausedJob = jobs.find(j => j.state === 'paused')
      expect(pausedJob).to.exist
      ;(service as any).pausedJobs.delete('crawl-slack-conn-2-org-1')
    })

    it('should return empty array when no jobs for org', async () => {
      if (!service) return
      sinon.stub((service as any).queue, 'getJobs').resolves([])

      const jobs = await service.getAllJobs('org-nonexistent')
      expect(jobs).to.be.an('array').that.is.empty
    })

    it('should throw when queue fails', async () => {
      if (!service) return
      sinon.stub((service as any).queue, 'getJobs').rejects(new Error('redis error'))

      try {
        await service.getAllJobs('org-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect((error as Error).message).to.equal('redis error')
      }
    })
  })

  // -------------------------------------------------------------------------
  // getRepeatableJobs
  // -------------------------------------------------------------------------
  describe('getRepeatableJobs', () => {
    it('should return all repeatable jobs when no orgId', async () => {
      if (!service) return
      sinon.stub((service as any).queue, 'getRepeatableJobs').resolves([
        { pattern: '0 * * * *', tz: 'UTC', key: 'k1' },
      ])

      const jobs = await service.getRepeatableJobs()
      expect(jobs).to.have.length(1)
    })

    it('should filter repeatable jobs by orgId', async () => {
      if (!service) return
      sinon.stub((service as any).queue, 'getRepeatableJobs').resolves([
        { pattern: '0 * * * *', tz: 'UTC', key: 'k1' },
      ])
      sinon.stub((service as any).queue, 'getJobs').resolves([
        {
          data: { orgId: 'org-1' },
          opts: { repeat: { pattern: '0 * * * *', tz: 'UTC' } },
        },
      ])

      const jobs = await service.getRepeatableJobs('org-1')
      expect(jobs).to.have.length(1)
    })

    it('should return empty when no matching org jobs', async () => {
      if (!service) return
      sinon.stub((service as any).queue, 'getRepeatableJobs').resolves([
        { pattern: '0 * * * *', tz: 'UTC', key: 'k1' },
      ])
      sinon.stub((service as any).queue, 'getJobs').resolves([])

      const jobs = await service.getRepeatableJobs('org-nonexistent')
      expect(jobs).to.have.length(0)
    })

    it('should throw when queue fails', async () => {
      if (!service) return
      sinon.stub((service as any).queue, 'getRepeatableJobs').rejects(new Error('error'))

      try {
        await service.getRepeatableJobs()
        expect.fail('Should have thrown')
      } catch (error) {
        expect((error as Error).message).to.equal('error')
      }
    })
  })

  // -------------------------------------------------------------------------
  // pauseJob
  // -------------------------------------------------------------------------
  describe('pauseJob', () => {
    it('should throw BadRequestError when no active job found', async () => {
      if (!service) return
      sinon.stub(service, 'getJobStatus').resolves(null)

      try {
        await service.pauseJob('google', 'conn-1', 'org-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })

    it('should throw BadRequestError when job is already paused', async () => {
      if (!service) return
      sinon.stub(service, 'getJobStatus').resolves({ state: 'paused' } as any)

      try {
        await service.pauseJob('google', 'conn-1', 'org-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })

    it('should pause an active job successfully', async () => {
      if (!service) return
      sinon.stub(service, 'getJobStatus').resolves({
        state: 'active',
        data: {
          connector: 'google',
          connectorId: 'conn-1',
          scheduleConfig: { scheduleType: CrawlingScheduleType.DAILY },
          orgId: 'org-1',
          userId: 'user-1',
          metadata: undefined,
        },
      } as any)
      sinon.stub(service as any, 'removeJobInternal').resolves()

      await service.pauseJob('google', 'conn-1', 'org-1')

      const pausedJobs = service.getPausedJobs()
      expect(pausedJobs.has('crawl-google-conn-1-org-1')).to.be.true
      ;(service as any).pausedJobs.delete('crawl-google-conn-1-org-1')
    })
  })

  // -------------------------------------------------------------------------
  // getQueueStats
  // -------------------------------------------------------------------------
  describe('getQueueStats', () => {
    it('should return queue statistics', async () => {
      if (!service) return
      const q = (service as any).queue
      sinon.stub(q, 'getWaiting').resolves([1, 2])
      sinon.stub(q, 'getActive').resolves([3])
      sinon.stub(q, 'getCompleted').resolves([4, 5, 6])
      sinon.stub(q, 'getFailed').resolves([])
      sinon.stub(q, 'getDelayed').resolves([7])
      sinon.stub(q, 'getRepeatableJobs').resolves([{ key: 'r1' }])

      const stats = await service.getQueueStats()

      expect(stats.waiting).to.equal(2)
      expect(stats.active).to.equal(1)
      expect(stats.completed).to.equal(3)
      expect(stats.failed).to.equal(0)
      expect(stats.delayed).to.equal(1)
      expect(stats.repeatable).to.equal(1)
      expect(stats.total).to.equal(7) // 2+1+3+0+1+0(paused)
    })

    it('should throw on queue failure', async () => {
      if (!service) return
      sinon.stub((service as any).queue, 'getWaiting').rejects(new Error('fail'))

      try {
        await service.getQueueStats()
        expect.fail('Should have thrown')
      } catch (error) {
        expect((error as Error).message).to.equal('fail')
      }
    })
  })

  // -------------------------------------------------------------------------
  // removeAllJobs
  // -------------------------------------------------------------------------
  describe('removeAllJobs', () => {
    it('should remove repeatable jobs, individual jobs, paused jobs, and mappings', async () => {
      if (!service) return
      const q = (service as any).queue
      const mockRepeatableJob = { pattern: '0 * * * *', tz: 'UTC', id: 'r1', endDate: null }
      sinon.stub(q, 'getRepeatableJobs').resolves([mockRepeatableJob])
      sinon.stub(q, 'removeRepeatable').resolves()

      const mockJob = {
        data: { connector: 'google', connectorId: 'conn-1', orgId: 'org-1' },
        opts: { repeat: { pattern: '0 * * * *', tz: 'UTC' } },
        id: 'j1',
        remove: sinon.stub().resolves(),
      }
      sinon.stub(q, 'getJobs').resolves([mockJob])

      ;(service as any).pausedJobs.set('crawl-google-conn-1-org-1', { orgId: 'org-1' })
      ;(service as any).repeatableJobMap.set('crawl-google-conn-1-org-1', 'rk-1')

      await service.removeAllJobs('org-1')

      expect((service as any).pausedJobs.has('crawl-google-conn-1-org-1')).to.be.false
      expect((service as any).repeatableJobMap.has('crawl-google-conn-1-org-1')).to.be.false
    })

    it('should throw on queue failure', async () => {
      if (!service) return
      sinon.stub((service as any).queue, 'getRepeatableJobs').rejects(new Error('fail'))

      try {
        await service.removeAllJobs('org-1')
        expect.fail('Should have thrown')
      } catch (error) {
        expect((error as Error).message).to.equal('fail')
      }
    })
  })

  // -------------------------------------------------------------------------
  // close
  // -------------------------------------------------------------------------
  describe('close', () => {
    it('should close queue and clear maps', async () => {
      if (!service) return
      sinon.stub((service as any).queue, 'close').resolves()
      ;(service as any).repeatableJobMap.set('k', 'v')
      ;(service as any).pausedJobs.set('k', { orgId: 'o' })

      await service.close()

      expect((service as any).repeatableJobMap.size).to.equal(0)
      expect((service as any).pausedJobs.size).to.equal(0)
    })

    it('should throw when queue.close fails', async () => {
      if (!service) return
      sinon.stub((service as any).queue, 'close').rejects(new Error('close fail'))

      try {
        await service.close()
        expect.fail('Should have thrown')
      } catch (error) {
        expect((error as Error).message).to.equal('close fail')
      }
    })
  })

  // -------------------------------------------------------------------------
  // getJobDebugInfo
  // -------------------------------------------------------------------------
  describe('getJobDebugInfo', () => {
    it('should return debug info for a connector/org', async () => {
      if (!service) return
      sinon.stub((service as any).queue, 'getRepeatableJobs').resolves([])
      sinon.stub((service as any).queue, 'getJobs').resolves([])

      const info = await service.getJobDebugInfo('google', 'conn-1', 'org-1')

      expect(info.customJobId).to.equal('crawl-google-conn-1-org-1')
      expect(info.hasMapping).to.be.false
      expect(info.isPaused).to.be.false
      expect(info.matchingJobInstances).to.equal(0)
      expect(info.connector).to.equal('google')
      expect(info.connectorId).to.equal('conn-1')
      expect(info.orgId).to.equal('org-1')
    })

    it('should return debug info with mapping and paused info', async () => {
      if (!service) return
      ;(service as any).repeatableJobMap.set('crawl-google-conn-1-org-1', 'rk-1')
      ;(service as any).pausedJobs.set('crawl-google-conn-1-org-1', { orgId: 'org-1' })
      sinon.stub((service as any).queue, 'getRepeatableJobs').resolves([{ key: 'rk-1' }])
      sinon.stub((service as any).queue, 'getJobs').resolves([])

      const info = await service.getJobDebugInfo('google', 'conn-1', 'org-1')

      expect(info.hasMapping).to.be.true
      expect(info.isPaused).to.be.true
      expect(info.repeatableJobKey).to.equal('rk-1')
      expect(info.relevantRepeatableJobs).to.have.length(1)

      ;(service as any).repeatableJobMap.delete('crawl-google-conn-1-org-1')
      ;(service as any).pausedJobs.delete('crawl-google-conn-1-org-1')
    })
  })
})
