import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  scheduleCrawlingJob,
  getCrawlingJobStatus,
  removeCrawlingJob,
  getAllCrawlingJobStatus,
  removeAllCrawlingJob,
  pauseCrawlingJob,
  resumeCrawlingJob,
  getQueueStats,
  handleConnectorResponse,
} from '../../../../src/modules/crawling_manager/controller/cm_controller'
import { NotFoundError } from '../../../../src/libs/errors/http.errors'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function createMockRequest(overrides: Record<string, any> = {}): any {
  return {
    headers: { authorization: 'Bearer test-token' },
    body: {},
    params: {},
    query: {},
    user: { userId: 'user-1', orgId: 'org-1' },
    ...overrides,
  }
}

function createMockResponse(): any {
  const res: any = {
    status: sinon.stub(),
    json: sinon.stub(),
    end: sinon.stub(),
    send: sinon.stub(),
    setHeader: sinon.stub(),
    getHeader: sinon.stub(),
    headersSent: false,
  }
  res.status.returns(res)
  res.json.returns(res)
  res.end.returns(res)
  return res
}

function createMockNext(): sinon.SinonStub {
  return sinon.stub()
}

function createMockCrawlingService(): any {
  return {
    scheduleJob: sinon.stub().resolves({ id: 'job-1' }),
    getJobStatus: sinon.stub().resolves(null),
    removeJob: sinon.stub().resolves(),
    getAllJobs: sinon.stub().resolves([]),
    removeAllJobs: sinon.stub().resolves(),
    pauseJob: sinon.stub().resolves(),
    resumeJob: sinon.stub().resolves(),
    getQueueStats: sinon.stub().resolves({
      waiting: 0,
      active: 0,
      completed: 0,
      failed: 0,
    }),
  }
}

function createMockAppConfig(): any {
  return {
    connectorBackend: 'http://localhost:8088',
  }
}

describe('Crawling Manager Controller', () => {
  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // handleConnectorResponse
  // -----------------------------------------------------------------------
  describe('handleConnectorResponse', () => {
    it('should throw when response status is not 200', () => {
      const connectorResponse = {
        statusCode: 500,
        data: { message: 'Error' },
      }

      expect(() => {
        handleConnectorResponse(connectorResponse, 'test operation', 'test failure')
      }).to.throw()
    })

    it('should throw NotFoundError when data is null', () => {
      const connectorResponse = {
        statusCode: 200,
        data: null,
      }

      expect(() => {
        handleConnectorResponse(connectorResponse, 'test op', 'not found')
      }).to.throw()
    })

    it('should return connector data on success', () => {
      const connectorResponse = {
        statusCode: 200,
        data: {
          connector: { _key: 'c-1', scope: 'team', createdBy: 'user-1' },
        },
      }

      const result = handleConnectorResponse(connectorResponse, 'get connector', 'not found')
      expect(result).to.have.property('_key', 'c-1')
    })

    it('should return connector with personal scope', () => {
      const connectorResponse = {
        statusCode: 200,
        data: {
          connector: { _key: 'c-2', scope: 'personal', createdBy: 'user-2' },
        },
      }

      const result = handleConnectorResponse(connectorResponse, 'get connector', 'not found')
      expect(result).to.have.property('scope', 'personal')
    })

    it('should handle 400 status code', () => {
      const connectorResponse = {
        statusCode: 400,
        data: { message: 'Bad request' },
      }

      expect(() => {
        handleConnectorResponse(connectorResponse, 'create', 'failed')
      }).to.throw()
    })

    it('should handle 404 status code', () => {
      const connectorResponse = {
        statusCode: 404,
        data: { message: 'Not found' },
      }

      expect(() => {
        handleConnectorResponse(connectorResponse, 'get', 'not found')
      }).to.throw()
    })
  })

  // -----------------------------------------------------------------------
  // scheduleCrawlingJob
  // -----------------------------------------------------------------------
  describe('scheduleCrawlingJob', () => {
    it('should return a handler function', () => {
      const handler = scheduleCrawlingJob(createMockCrawlingService(), createMockAppConfig())
      expect(handler).to.be.a('function')
    })

    it('should call next when connectorId is missing', async () => {
      const handler = scheduleCrawlingJob(createMockCrawlingService(), createMockAppConfig())
      const req = createMockRequest({
        params: { connector: 'google', connectorId: '' },
        body: {
          scheduleConfig: { scheduleType: 'daily', hour: 2, minute: 0 },
          priority: 1,
          maxRetries: 3,
        },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should call next when body is missing required fields', async () => {
      const handler = scheduleCrawlingJob(createMockCrawlingService(), createMockAppConfig())
      const req = createMockRequest({
        params: { connector: 'google', connectorId: 'conn-1' },
        body: {},
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      // Will fail when trying to validate connector access or missing scheduleConfig
      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // getCrawlingJobStatus
  // -----------------------------------------------------------------------
  describe('getCrawlingJobStatus', () => {
    it('should return a handler function', () => {
      const handler = getCrawlingJobStatus(createMockCrawlingService(), createMockAppConfig())
      expect(handler).to.be.a('function')
    })
  })

  // -----------------------------------------------------------------------
  // removeCrawlingJob
  // -----------------------------------------------------------------------
  describe('removeCrawlingJob', () => {
    it('should return a handler function', () => {
      const handler = removeCrawlingJob(createMockCrawlingService(), createMockAppConfig())
      expect(handler).to.be.a('function')
    })
  })

  // -----------------------------------------------------------------------
  // getAllCrawlingJobStatus
  // -----------------------------------------------------------------------
  describe('getAllCrawlingJobStatus', () => {
    it('should return a handler function', () => {
      const handler = getAllCrawlingJobStatus(createMockCrawlingService())
      expect(handler).to.be.a('function')
    })

    it('should return all job statuses for an org', async () => {
      const mockService = createMockCrawlingService()
      mockService.getAllJobs.resolves([
        { connector: 'google', status: 'active' },
        { connector: 'slack', status: 'waiting' },
      ])
      const handler = getAllCrawlingJobStatus(mockService)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.success).to.be.true
      expect(response.data).to.have.length(2)
    })

    it('should return empty array when no jobs exist', async () => {
      const mockService = createMockCrawlingService()
      mockService.getAllJobs.resolves([])
      const handler = getAllCrawlingJobStatus(mockService)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.data).to.have.length(0)
    })

    it('should call next on error', async () => {
      const mockService = createMockCrawlingService()
      mockService.getAllJobs.rejects(new Error('Redis error'))
      const handler = getAllCrawlingJobStatus(mockService)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should pass orgId from request user', async () => {
      const mockService = createMockCrawlingService()
      const handler = getAllCrawlingJobStatus(mockService)
      const req = createMockRequest({ user: { orgId: 'org-custom', userId: 'user-1' } })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(mockService.getAllJobs.calledWith('org-custom')).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // removeAllCrawlingJob
  // -----------------------------------------------------------------------
  describe('removeAllCrawlingJob', () => {
    it('should return a handler function', () => {
      const handler = removeAllCrawlingJob(createMockCrawlingService())
      expect(handler).to.be.a('function')
    })

    it('should remove all jobs for an org', async () => {
      const mockService = createMockCrawlingService()
      const handler = removeAllCrawlingJob(mockService)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(mockService.removeAllJobs.calledOnce).to.be.true
      expect(mockService.removeAllJobs.firstCall.args[0]).to.equal('org-1')
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0].success).to.be.true
    })

    it('should call next on error', async () => {
      const mockService = createMockCrawlingService()
      mockService.removeAllJobs.rejects(new Error('Redis error'))
      const handler = removeAllCrawlingJob(mockService)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should include success message in response', async () => {
      const mockService = createMockCrawlingService()
      const handler = removeAllCrawlingJob(mockService)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      const response = res.json.firstCall.args[0]
      expect(response.message).to.include('removed successfully')
    })
  })

  // -----------------------------------------------------------------------
  // pauseCrawlingJob
  // -----------------------------------------------------------------------
  describe('pauseCrawlingJob', () => {
    it('should return a handler function', () => {
      const handler = pauseCrawlingJob(createMockCrawlingService(), createMockAppConfig())
      expect(handler).to.be.a('function')
    })
  })

  // -----------------------------------------------------------------------
  // resumeCrawlingJob
  // -----------------------------------------------------------------------
  describe('resumeCrawlingJob', () => {
    it('should return a handler function', () => {
      const handler = resumeCrawlingJob(createMockCrawlingService(), createMockAppConfig())
      expect(handler).to.be.a('function')
    })
  })

  // -----------------------------------------------------------------------
  // getQueueStats
  // -----------------------------------------------------------------------
  describe('getQueueStats', () => {
    it('should return a handler function', () => {
      const handler = getQueueStats(createMockCrawlingService())
      expect(handler).to.be.a('function')
    })

    it('should return queue statistics', async () => {
      const mockService = createMockCrawlingService()
      const stats = { waiting: 5, active: 2, completed: 100, failed: 3 }
      mockService.getQueueStats.resolves(stats)
      const handler = getQueueStats(mockService)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.success).to.be.true
      expect(response.data).to.deep.equal(stats)
    })

    it('should call next on error', async () => {
      const mockService = createMockCrawlingService()
      mockService.getQueueStats.rejects(new Error('Redis connection failed'))
      const handler = getQueueStats(mockService)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(next.calledOnce).to.be.true
    })

    it('should return zero counts when queue is empty', async () => {
      const mockService = createMockCrawlingService()
      const emptyStats = { waiting: 0, active: 0, completed: 0, failed: 0, delayed: 0, paused: 0, repeatable: 0, total: 0 }
      mockService.getQueueStats.resolves(emptyStats)
      const handler = getQueueStats(mockService)
      const req = createMockRequest()
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      const response = res.json.firstCall.args[0]
      expect(response.data.waiting).to.equal(0)
      expect(response.data.active).to.equal(0)
    })
  })

  // -----------------------------------------------------------------------
  // getCrawlingJobStatus – happy + not-found + error paths
  // -----------------------------------------------------------------------
  describe('getCrawlingJobStatus (extended)', () => {
    it('should return 404 when no job found', async () => {
      const mockService = createMockCrawlingService()
      mockService.getJobStatus.resolves(null)
      // Stub validateConnectorAccess dependency
      const connectorUtils = require('../../../../src/modules/tokens_manager/utils/connector-service-http')
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { connector: { scope: 'team', createdBy: 'user-1' } },
      })
      const isAdminStub = sinon.stub(
        require('../../../../src/modules/tokens_manager/controllers/connector.controllers'),
        'isUserAdmin',
      ).resolves(true)

      const handler = getCrawlingJobStatus(mockService, createMockAppConfig())
      const req = createMockRequest({
        params: { connector: 'google', connectorId: 'conn-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(404)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.success).to.be.false
    })

    it('should return job status when found', async () => {
      const mockService = createMockCrawlingService()
      const jobStatus = { id: 'j-1', state: 'active', connector: 'google' }
      mockService.getJobStatus.resolves(jobStatus)
      const connectorUtils = require('../../../../src/modules/tokens_manager/utils/connector-service-http')
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { connector: { scope: 'team', createdBy: 'user-1' } },
      })
      sinon.stub(
        require('../../../../src/modules/tokens_manager/controllers/connector.controllers'),
        'isUserAdmin',
      ).resolves(true)

      const handler = getCrawlingJobStatus(mockService, createMockAppConfig())
      const req = createMockRequest({
        params: { connector: 'google', connectorId: 'conn-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.success).to.be.true
      expect(response.data).to.deep.equal(jobStatus)
    })

    it('should call next on error', async () => {
      const mockService = createMockCrawlingService()
      mockService.getJobStatus.rejects(new Error('Redis error'))
      const connectorUtils = require('../../../../src/modules/tokens_manager/utils/connector-service-http')
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { connector: { scope: 'team', createdBy: 'user-1' } },
      })
      sinon.stub(
        require('../../../../src/modules/tokens_manager/controllers/connector.controllers'),
        'isUserAdmin',
      ).resolves(true)

      const handler = getCrawlingJobStatus(mockService, createMockAppConfig())
      const req = createMockRequest({
        params: { connector: 'google', connectorId: 'conn-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // removeCrawlingJob – happy + error paths
  // -----------------------------------------------------------------------
  describe('removeCrawlingJob (extended)', () => {
    it('should remove job successfully', async () => {
      const mockService = createMockCrawlingService()
      const connectorUtils = require('../../../../src/modules/tokens_manager/utils/connector-service-http')
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { connector: { scope: 'team', createdBy: 'user-1' } },
      })
      sinon.stub(
        require('../../../../src/modules/tokens_manager/controllers/connector.controllers'),
        'isUserAdmin',
      ).resolves(true)

      const handler = removeCrawlingJob(mockService, createMockAppConfig())
      const req = createMockRequest({
        params: { connector: 'google', connectorId: 'conn-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(mockService.removeJob.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should call next on remove error', async () => {
      const mockService = createMockCrawlingService()
      mockService.removeJob.rejects(new Error('fail'))
      const connectorUtils = require('../../../../src/modules/tokens_manager/utils/connector-service-http')
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { connector: { scope: 'team', createdBy: 'user-1' } },
      })
      sinon.stub(
        require('../../../../src/modules/tokens_manager/controllers/connector.controllers'),
        'isUserAdmin',
      ).resolves(true)

      const handler = removeCrawlingJob(mockService, createMockAppConfig())
      const req = createMockRequest({
        params: { connector: 'google', connectorId: 'conn-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // pauseCrawlingJob – happy + error paths
  // -----------------------------------------------------------------------
  describe('pauseCrawlingJob (extended)', () => {
    it('should pause job successfully', async () => {
      const mockService = createMockCrawlingService()
      const connectorUtils = require('../../../../src/modules/tokens_manager/utils/connector-service-http')
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { connector: { scope: 'team', createdBy: 'user-1' } },
      })
      sinon.stub(
        require('../../../../src/modules/tokens_manager/controllers/connector.controllers'),
        'isUserAdmin',
      ).resolves(true)

      const handler = pauseCrawlingJob(mockService, createMockAppConfig())
      const req = createMockRequest({
        params: { connector: 'google', connectorId: 'conn-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(mockService.pauseJob.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.success).to.be.true
      expect(response.data.connector).to.equal('google')
    })

    it('should call next on pause error', async () => {
      const mockService = createMockCrawlingService()
      mockService.pauseJob.rejects(new Error('pause failed'))
      const connectorUtils = require('../../../../src/modules/tokens_manager/utils/connector-service-http')
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { connector: { scope: 'team', createdBy: 'user-1' } },
      })
      sinon.stub(
        require('../../../../src/modules/tokens_manager/controllers/connector.controllers'),
        'isUserAdmin',
      ).resolves(true)

      const handler = pauseCrawlingJob(mockService, createMockAppConfig())
      const req = createMockRequest({
        params: { connector: 'google', connectorId: 'conn-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // resumeCrawlingJob – happy + error paths
  // -----------------------------------------------------------------------
  describe('resumeCrawlingJob (extended)', () => {
    it('should resume job successfully', async () => {
      const mockService = createMockCrawlingService()
      const connectorUtils = require('../../../../src/modules/tokens_manager/utils/connector-service-http')
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { connector: { scope: 'team', createdBy: 'user-1' } },
      })
      sinon.stub(
        require('../../../../src/modules/tokens_manager/controllers/connector.controllers'),
        'isUserAdmin',
      ).resolves(true)

      const handler = resumeCrawlingJob(mockService, createMockAppConfig())
      const req = createMockRequest({
        params: { connector: 'google', connectorId: 'conn-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)

      expect(mockService.resumeJob.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
      const response = res.json.firstCall.args[0]
      expect(response.success).to.be.true
      expect(response.data.connector).to.equal('google')
    })

    it('should call next on resume error', async () => {
      const mockService = createMockCrawlingService()
      mockService.resumeJob.rejects(new Error('resume failed'))
      const connectorUtils = require('../../../../src/modules/tokens_manager/utils/connector-service-http')
      sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({
        statusCode: 200,
        data: { connector: { scope: 'team', createdBy: 'user-1' } },
      })
      sinon.stub(
        require('../../../../src/modules/tokens_manager/controllers/connector.controllers'),
        'isUserAdmin',
      ).resolves(true)

      const handler = resumeCrawlingJob(mockService, createMockAppConfig())
      const req = createMockRequest({
        params: { connector: 'google', connectorId: 'conn-1' },
      })
      const res = createMockResponse()
      const next = createMockNext()

      await handler(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })
})
