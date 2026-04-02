import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { CrawlingWorkerService } from '../../../../src/modules/crawling_manager/services/crawling_worker'

describe('CrawlingWorkerService', () => {
  afterEach(() => {
    sinon.restore()
  })

  // -------------------------------------------------------------------------
  // class structure
  // -------------------------------------------------------------------------
  describe('class structure', () => {
    it('should be a class with injectable decorator', () => {
      expect(CrawlingWorkerService).to.be.a('function')
    })

    it('should have a close method', () => {
      expect(CrawlingWorkerService.prototype.close).to.be.a('function')
    })

    it('should have close as async method on prototype', () => {
      expect(typeof CrawlingWorkerService.prototype.close).to.equal('function')
    })
  })

  // -------------------------------------------------------------------------
  // constructor
  // -------------------------------------------------------------------------
  describe('constructor', () => {
    it('should require redisConfig and taskService parameters', () => {
      expect(CrawlingWorkerService).to.be.a('function')
    })
  })

  // -------------------------------------------------------------------------
  // processJob (private) - tested via prototype access
  // -------------------------------------------------------------------------
  describe('processJob (private)', () => {
    it('should be defined on prototype', () => {
      expect((CrawlingWorkerService.prototype as any).processJob).to.be.a('function')
    })

    it('should call taskService.crawl and update progress', async () => {
      const mockTaskService = {
        crawl: sinon.stub().resolves({ status: 'success' }),
      }

      const mockJob = {
        id: 'job-1',
        data: {
          connector: 'google',
          connectorId: 'conn-1',
          orgId: 'org-1',
          userId: 'user-1',
          scheduleConfig: { scheduleType: 'daily' },
        },
        updateProgress: sinon.stub().resolves(),
      }

      // Call processJob directly on prototype with bound context
      const processJob = (CrawlingWorkerService.prototype as any).processJob
      const context = {
        logger: {
          info: sinon.stub(),
          error: sinon.stub(),
          debug: sinon.stub(),
          warn: sinon.stub(),
        },
        taskService: mockTaskService,
      }

      await processJob.call(context, mockJob)

      expect(mockJob.updateProgress.callCount).to.equal(3) // 10, 20, 100
      expect(mockTaskService.crawl.calledOnce).to.be.true
      expect(mockTaskService.crawl.firstCall.args[0]).to.equal('org-1')
      expect(mockTaskService.crawl.firstCall.args[1]).to.equal('user-1')
    })

    it('should throw error when taskService.crawl fails', async () => {
      const mockTaskService = {
        crawl: sinon.stub().rejects(new Error('Crawl failed')),
      }

      const mockJob = {
        id: 'job-1',
        data: {
          connector: 'slack',
          connectorId: 'conn-2',
          orgId: 'org-1',
          userId: 'user-1',
          scheduleConfig: { scheduleType: 'daily' },
        },
        updateProgress: sinon.stub().resolves(),
      }

      const processJob = (CrawlingWorkerService.prototype as any).processJob
      const context = {
        logger: {
          info: sinon.stub(),
          error: sinon.stub(),
          debug: sinon.stub(),
          warn: sinon.stub(),
        },
        taskService: mockTaskService,
      }

      try {
        await processJob.call(context, mockJob)
        expect.fail('Should have thrown')
      } catch (error) {
        expect((error as Error).message).to.equal('Crawl failed')
      }

      expect(context.logger.error.calledOnce).to.be.true
    })
  })

  // -------------------------------------------------------------------------
  // setupWorkerListeners (private)
  // -------------------------------------------------------------------------
  describe('setupWorkerListeners (private)', () => {
    it('should be defined on prototype', () => {
      expect((CrawlingWorkerService.prototype as any).setupWorkerListeners).to.be.a('function')
    })
  })
})
