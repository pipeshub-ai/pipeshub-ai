import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { OrgController } from '../../../../src/modules/user_management/controller/org.controller'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { OrgLogos } from '../../../../src/modules/user_management/schema/orgLogo.schema'
import {
  BadRequestError,
  NotFoundError,
} from '../../../../src/libs/errors/http.errors'
import {
  createMockOrgDocumentForSoftDelete,
  createMockOrgUpdatedDocument,
} from '../../../helpers/mock-org-updated-document'

describe('OrgController - additional coverage 2', () => {
  let controller: OrgController
  let mockConfig: any
  let mockMailService: any
  let mockLogger: any
  let mockEventService: any
  let req: any
  let res: any
  let next: sinon.SinonStub

  beforeEach(() => {
    mockConfig = {
      frontendUrl: 'http://localhost:3000',
      scopedJwtSecret: 'test-secret',
      rsAvailable: 'false',
    }
    mockMailService = {
      sendMail: sinon.stub().resolves({ statusCode: 200, data: {} }),
    }
    mockLogger = {
      debug: sinon.stub(),
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
    }
    mockEventService = {
      start: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(false),
    }
    controller = new OrgController(
      mockConfig,
      mockMailService,
      mockLogger,
      mockEventService,
    )
    req = {
      user: {
        userId: '507f1f77bcf86cd799439011',
        orgId: '507f1f77bcf86cd799439012',
      },
      params: {},
      query: {},
      body: {},
      headers: {},
      ip: '127.0.0.1',
      method: 'POST',
      path: '/org',
      context: { requestId: 'test-request-id' },
    }
    res = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
      send: sinon.stub().returnsThis(),
      setHeader: sinon.stub().returnsThis(),
      end: sinon.stub().returnsThis(),
    }
    next = sinon.stub()
  })

  afterEach(() => {
    sinon.restore()
  })

  // -----------------------------------------------------------------------
  // getDomainFromEmail
  // -----------------------------------------------------------------------
  describe('getDomainFromEmail', () => {
    it('should return domain from valid email', () => {
      const result = controller.getDomainFromEmail('test@example.com')
      expect(result).to.equal('example.com')
    })

    it('should return null for email without @', () => {
      const result = controller.getDomainFromEmail('invalid')
      expect(result).to.be.null
    })

    it('should return null for email with multiple @', () => {
      const result = controller.getDomainFromEmail('a@b@c.com')
      expect(result).to.be.null
    })

    it('should return domain for standard email', () => {
      const result = controller.getDomainFromEmail('user@company.org')
      expect(result).to.equal('company.org')
    })
  })

  // -----------------------------------------------------------------------
  // validateSVG (private - tested through updateOrgLogo)
  // -----------------------------------------------------------------------
  describe('SVG validation via updateOrgLogo', () => {
    it('should reject SVG with script tags', async () => {
      req.body = {
        fileBuffer: {
          buffer: Buffer.from('<svg><script>alert(1)</script></svg>'),
          mimetype: 'image/svg+xml',
        },
      }
      await controller.updateOrgLogo(req, res, next)
      expect(next.calledOnce).to.be.true
      const err = next.firstCall.args[0]
      expect(err).to.be.instanceOf(BadRequestError)
    })

    it('should reject SVG with event handlers', async () => {
      req.body = {
        fileBuffer: {
          buffer: Buffer.from('<svg onload="alert(1)"></svg>'),
          mimetype: 'image/svg+xml',
        },
      }
      await controller.updateOrgLogo(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should reject SVG with javascript: protocol', async () => {
      req.body = {
        fileBuffer: {
          buffer: Buffer.from('<svg><a href="javascript:alert(1)"></a></svg>'),
          mimetype: 'image/svg+xml',
        },
      }
      await controller.updateOrgLogo(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should reject SVG with data:text/html', async () => {
      req.body = {
        fileBuffer: {
          buffer: Buffer.from('<svg><image href="data: text/html,<script>alert(1)</script>"></image></svg>'),
          mimetype: 'image/svg+xml',
        },
      }
      await controller.updateOrgLogo(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should reject SVG with iframe', async () => {
      req.body = {
        fileBuffer: {
          buffer: Buffer.from('<svg><iframe src="evil.com"></iframe></svg>'),
          mimetype: 'image/svg+xml',
        },
      }
      await controller.updateOrgLogo(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should reject SVG with object tag', async () => {
      req.body = {
        fileBuffer: {
          buffer: Buffer.from('<svg><object data="evil.swf"></object></svg>'),
          mimetype: 'image/svg+xml',
        },
      }
      await controller.updateOrgLogo(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should reject SVG with embed tag', async () => {
      req.body = {
        fileBuffer: {
          buffer: Buffer.from('<svg><embed src="evil.swf"></embed></svg>'),
          mimetype: 'image/svg+xml',
        },
      }
      await controller.updateOrgLogo(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should reject oversized SVG (>10MB)', async () => {
      const largeSvg = Buffer.alloc(11 * 1024 * 1024, 'a')
      req.body = {
        fileBuffer: {
          buffer: largeSvg,
          mimetype: 'image/svg+xml',
        },
      }
      await controller.updateOrgLogo(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should accept clean SVG', async () => {
      const cleanSvg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="40"/></svg>'
      req.body = {
        fileBuffer: {
          buffer: Buffer.from(cleanSvg),
          mimetype: 'image/svg+xml',
        },
      }

      sinon.stub(OrgLogos, 'findOneAndUpdate').resolves({} as any)

      await controller.updateOrgLogo(req, res, next)
      expect(res.status.calledWith(201)).to.be.true
    })

    it('should throw when no fileBuffer', async () => {
      req.body = {}
      await controller.updateOrgLogo(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // getOrgLogo
  // -----------------------------------------------------------------------
  describe('getOrgLogo', () => {
    it('should return 204 when no logo found', async () => {
      sinon.stub(OrgLogos, 'findOne').returns({
        lean: sinon.stub().returns({ exec: sinon.stub().resolves(null) }),
      } as any)

      await controller.getOrgLogo(req, res, next)
      expect(res.status.calledWith(204)).to.be.true
    })

    it('should return 204 when logo field is empty', async () => {
      sinon.stub(OrgLogos, 'findOne').returns({
        lean: sinon.stub().returns({ exec: sinon.stub().resolves({ logo: null }) }),
      } as any)

      await controller.getOrgLogo(req, res, next)
      expect(res.status.calledWith(204)).to.be.true
    })

    it('should return logo buffer with content type', async () => {
      const base64Logo = Buffer.from('test-logo').toString('base64')
      sinon.stub(OrgLogos, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves({ logo: base64Logo, mimeType: 'image/jpeg' }),
        }),
      } as any)

      await controller.getOrgLogo(req, res, next)
      expect(res.setHeader.calledWith('Content-Type', 'image/jpeg')).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should return logo without mimeType header when not set', async () => {
      const base64Logo = Buffer.from('test-logo').toString('base64')
      sinon.stub(OrgLogos, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves({ logo: base64Logo, mimeType: null }),
        }),
      } as any)

      await controller.getOrgLogo(req, res, next)
      expect(res.setHeader.called).to.be.false
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // removeOrgLogo
  // -----------------------------------------------------------------------
  describe('removeOrgLogo', () => {
    it('should throw NotFoundError when no logo exists', async () => {
      sinon.stub(OrgLogos, 'findOne').returns({
        exec: sinon.stub().resolves(null),
      } as any)

      await controller.removeOrgLogo(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should remove logo and return updated record', async () => {
      const mockOrgLogo = {
        logo: 'some-base64',
        mimeType: 'image/png',
        save: sinon.stub().resolves(),
        toJSON() {
          return {
            logo: this.logo,
            mimeType: this.mimeType,
            _id: '507f1f77bcf86cd799439011',
            orgId: '507f1f77bcf86cd799439012',
            __v: 0,
          }
        },
      }
      sinon.stub(OrgLogos, 'findOne').returns({
        exec: sinon.stub().resolves(mockOrgLogo),
      } as any)

      await controller.removeOrgLogo(req, res, next)
      expect(mockOrgLogo.logo).to.be.null
      expect(mockOrgLogo.mimeType).to.be.null
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // getOnboardingStatus
  // -----------------------------------------------------------------------
  describe('getOnboardingStatus', () => {
    it('should return onboarding status', async () => {
      sinon.stub(Org, 'findOne').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().resolves({ onBoardingStatus: 'configured' }),
        }),
      } as any)

      await controller.getOnboardingStatus(req, res, next)
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should default to notConfigured', async () => {
      sinon.stub(Org, 'findOne').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().resolves({ onBoardingStatus: null }),
        }),
      } as any)

      await controller.getOnboardingStatus(req, res, next)
      const jsonArg = res.json.firstCall.args[0]
      expect(jsonArg.status).to.equal('notConfigured')
    })

    it('should throw NotFoundError when org not found', async () => {
      sinon.stub(Org, 'findOne').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().resolves(null),
        }),
      } as any)

      await controller.getOnboardingStatus(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // updateOnboardingStatus
  // -----------------------------------------------------------------------
  describe('updateOnboardingStatus', () => {
    it('should reject invalid status', async () => {
      req.body = { status: 'invalid' }
      await controller.updateOnboardingStatus(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should update valid status', async () => {
      req.body = { status: 'configured' }
      const mockOrg = {
        onBoardingStatus: 'notConfigured',
        save: sinon.stub().resolves(),
      }
      sinon.stub(Org, 'findOne').resolves(mockOrg as any)

      await controller.updateOnboardingStatus(req, res, next)
      expect(mockOrg.onBoardingStatus).to.equal('configured')
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should throw NotFoundError when org not found', async () => {
      req.body = { status: 'skipped' }
      sinon.stub(Org, 'findOne').resolves(null)

      await controller.updateOnboardingStatus(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // deleteOrganization
  // -----------------------------------------------------------------------
  describe('deleteOrganization', () => {
    it('should soft delete organization', async () => {
      const mockOrg = {
        ...createMockOrgDocumentForSoftDelete({ _id: 'org1' }),
        save: sinon.stub().resolves(),
      }
      sinon.stub(Org, 'findOne').resolves(mockOrg as any)

      await controller.deleteOrganization(req, res, next)
      expect(mockOrg.isDeleted).to.be.true
      expect(mockEventService.publishEvent.calledOnce).to.be.true
    })

    it('should throw NotFoundError when org not found', async () => {
      sinon.stub(Org, 'findOne').resolves(null)

      await controller.deleteOrganization(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // checkOrgExistence
  // -----------------------------------------------------------------------
  describe('checkOrgExistence', () => {
    it('should return exists: true when orgs exist', async () => {
      sinon.stub(Org, 'countDocuments').resolves(1)

      await controller.checkOrgExistence(res)
      expect(res.json.calledWith({ exists: true })).to.be.true
    })

    it('should return exists: false when no orgs', async () => {
      sinon.stub(Org, 'countDocuments').resolves(0)

      await controller.checkOrgExistence(res)
      expect(res.json.calledWith({ exists: false })).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // getOrganizationById
  // -----------------------------------------------------------------------
  describe('getOrganizationById', () => {
    it('should throw NotFoundError when org not found', async () => {
      sinon.stub(Org, 'findOne').resolves(null)

      await controller.getOrganizationById(req, res, next)
      expect(next.calledOnce).to.be.true
    })

    it('should return org when found', async () => {
      const mockOrg = createMockOrgUpdatedDocument({
        _id: 'org1',
        registeredName: 'Test Org',
      })
      sinon.stub(Org, 'findOne').resolves(mockOrg as any)

      await controller.getOrganizationById(req, res, next)
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  // -----------------------------------------------------------------------
  // updateOrganizationDetails - contactEmail and registeredName
  // -----------------------------------------------------------------------
  describe('updateOrganizationDetails', () => {
    it('should update contactEmail and registeredName', async () => {
      req.body = { contactEmail: 'new@test.com', registeredName: 'New Name' }
      const mockOrg = { _id: 'org1', registeredName: 'Old Name' }
      sinon.stub(Org, 'findOne').resolves(mockOrg as any)
      sinon.stub(Org, 'findByIdAndUpdate').resolves(
        createMockOrgUpdatedDocument({
          contactEmail: 'new@test.com',
          registeredName: 'New Name',
        }) as any,
      )

      await controller.updateOrganizationDetails(req, res, next)
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should throw NotFoundError when org not found', async () => {
      req.body = { contactEmail: 'test@test.com' }
      sinon.stub(Org, 'findOne').resolves(null)

      await controller.updateOrganizationDetails(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })
})
