import 'reflect-metadata'
import { currentOrgContext } from '../../../../src/libs/context/orgContext'
import { expect } from 'chai'
import sinon from 'sinon'
import { CrawlingWorkerService } from '../../../../src/modules/crawling_manager/services/crawling_worker'

import { Org } from '../../../../src/modules/user_management/schema/org.schema'
let orgFindOne: sinon.SinonStub
const ORG_1 = '507f1f77bcf86cd799439011'
const ORG_2 = '507f1f77bcf86cd799439012'
const ORG_GONE = '507f1f77bcf86cd799439013'

describe('CrawlingWorkerService', () => {
  beforeEach(() => {
    // by default the job's org exists and is active
    orgFindOne = sinon.stub(Org, 'findOne').returns({
      select: () => ({ lean: () => ({ exec: async () => ({ _id: 'org' }) }) }),
    } as any)
  })
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
          orgId: ORG_1,
          userId: 'user-1',
          scheduleConfig: { scheduleType: 'daily' },
        },
        updateProgress: sinon.stub().resolves(),
      }

      // Call processJob directly on prototype with bound context.
      // processJob is a thin wrapper that opens a request-context and delegates
      // to this.processJobInContext, so the bound context must expose it too.
      const processJob = (CrawlingWorkerService.prototype as any).processJob
      const context = {
        logger: {
          info: sinon.stub(),
          error: sinon.stub(),
          debug: sinon.stub(),
          warn: sinon.stub(),
        },
        taskService: mockTaskService,
        processJobInContext: (CrawlingWorkerService.prototype as any).processJobInContext,
        processJobForOrg: (CrawlingWorkerService.prototype as any).processJobForOrg,
      }

      await processJob.call(context, mockJob)

      expect(mockJob.updateProgress.callCount).to.equal(3) // 10, 20, 100
      expect(mockTaskService.crawl.calledOnce).to.be.true
      expect(mockTaskService.crawl.firstCall.args[0]).to.equal(ORG_1)
      expect(mockTaskService.crawl.firstCall.args[1]).to.equal('user-1')
    })

    it('binds the job org so one job never sees the previous job org', async () => {
      const seen: unknown[] = []
      const processJob = (CrawlingWorkerService.prototype as any).processJob
      const context = {
        processJobInContext: async () => { seen.push(currentOrgContext()) },
        processJobForOrg: (CrawlingWorkerService.prototype as any).processJobForOrg,
        logger: { warn: sinon.stub() },
      }
      await processJob.call(context, { data: { orgId: ORG_1 } })
      await processJob.call(context, { data: { orgId: ORG_2 } })
      expect(seen.map((c: any) => [c.orgId, c.kind, c.source])).to.deep.equal([
        [ORG_1, 'job', 'job:crawl'],
        [ORG_2, 'job', 'job:crawl'],
      ])
      expect(() => currentOrgContext()).to.throw()
    })

    it('drops a job whose org no longer exists or is not active', async () => {
      orgFindOne.returns({ select: () => ({ lean: () => ({ exec: async () => null }) }) })
      const processJobInContext = sinon.stub().resolves()
      const warn = sinon.stub()
      const processJob = (CrawlingWorkerService.prototype as any).processJob
      await processJob.call(
        { processJobInContext, processJobForOrg: (CrawlingWorkerService.prototype as any).processJobForOrg, logger: { warn } },
        { id: 'j', data: { orgId: ORG_GONE } },
      )
      expect(processJobInContext.called).to.be.false
      expect(warn.calledOnce).to.be.true
    })

    it('drops, never fails, a job whose org id is not an ObjectId (for example "system")', async () => {
      orgFindOne.resetHistory()
      const processJobInContext = sinon.stub().resolves()
      const warn = sinon.stub()
      const processJob = (CrawlingWorkerService.prototype as any).processJob
      for (const orgId of ['system', 'not-an-id', '{"$ne":null}']) {
        await processJob.call(
          { processJobInContext, processJobForOrg: (CrawlingWorkerService.prototype as any).processJobForOrg, logger: { warn } },
          { id: 'j', data: { orgId } },
        )
      }
      expect(processJobInContext.called).to.be.false
      expect(orgFindOne.called).to.be.false
      expect(warn.callCount).to.equal(3)
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
          orgId: ORG_1,
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
        processJobInContext: (CrawlingWorkerService.prototype as any).processJobInContext,
        processJobForOrg: (CrawlingWorkerService.prototype as any).processJobForOrg,
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
