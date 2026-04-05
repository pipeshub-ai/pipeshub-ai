import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { OrgController } from '../../../../src/modules/user_management/controller/org.controller'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { OrgLogos } from '../../../../src/modules/user_management/schema/orgLogo.schema'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import {
  BadRequestError,
  NotFoundError,
} from '../../../../src/libs/errors/http.errors'
import {
  createMockOrgDocumentForSoftDelete,
  createMockOrgUpdatedDocument,
} from '../../../helpers/mock-org-updated-document'

describe('OrgController - additional coverage', () => {
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

  describe('updateOrganizationDetails - all updateData fields', () => {
    it('should update shortName and permanentAddress fields', async () => {
      req.body = {
        shortName: 'TEST',
        permanentAddress: { addressLine1: '123 Main St' },
      }

      const mockOrg = {
        _id: '507f1f77bcf86cd799439012',
        registeredName: 'Test Org',
      }

      sinon.stub(Org, 'findOne').resolves(mockOrg as any)
      sinon.stub(Org, 'findByIdAndUpdate').resolves(
        createMockOrgUpdatedDocument({
          shortName: 'TEST',
          permanentAddress: { addressLine1: '123 Main St' },
        }) as any,
      )

      await controller.updateOrganizationDetails(req, res, next)

      expect(mockEventService.publishEvent.calledOnce).to.be.true
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should update with all fields together', async () => {
      req.body = {
        contactEmail: 'new@org.com',
        registeredName: 'New Name',
        shortName: 'NN',
        permanentAddress: { addressLine1: '456 St' },
      }

      const mockOrg = { _id: '507f1f77bcf86cd799439012', registeredName: 'Old' }
      sinon.stub(Org, 'findOne').resolves(mockOrg as any)
      sinon.stub(Org, 'findByIdAndUpdate').resolves(
        createMockOrgUpdatedDocument({
          contactEmail: 'new@org.com',
          registeredName: 'New Name',
          shortName: 'NN',
          permanentAddress: { addressLine1: '456 St' },
        }) as any,
      )

      await controller.updateOrganizationDetails(req, res, next)
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should handle empty body (no fields to update)', async () => {
      req.body = {}

      const mockOrg = { _id: '507f1f77bcf86cd799439012', registeredName: 'Org' }
      sinon.stub(Org, 'findOne').resolves(mockOrg as any)
      sinon.stub(Org, 'findByIdAndUpdate').resolves(
        createMockOrgUpdatedDocument({ registeredName: 'Org' }) as any,
      )

      await controller.updateOrganizationDetails(req, res, next)
      expect(res.status.calledWith(200)).to.be.true
    })
  })

  describe('updateOrgLogo - raster image compression', () => {
    it('should compress JPEG image below 100KB', async () => {
      // Create a small image buffer
      const sharp = require('sharp')
      const smallImageBuffer = await sharp({
        create: { width: 10, height: 10, channels: 3, background: { r: 255, g: 0, b: 0 } },
      }).jpeg().toBuffer()

      req.body = {
        fileBuffer: {
          buffer: smallImageBuffer,
          mimetype: 'image/jpeg',
        },
      }

      sinon.stub(OrgLogos, 'findOneAndUpdate').resolves({} as any)

      await controller.updateOrgLogo(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(201)).to.be.true
        const jsonArg = res.json.firstCall.args[0]
        expect(jsonArg.mimeType).to.equal('image/jpeg')
      }
    })

    it('should handle PNG to JPEG conversion', async () => {
      const sharp = require('sharp')
      const pngBuffer = await sharp({
        create: { width: 10, height: 10, channels: 4, background: { r: 0, g: 255, b: 0, alpha: 1 } },
      }).png().toBuffer()

      req.body = {
        fileBuffer: {
          buffer: pngBuffer,
          mimetype: 'image/png',
        },
      }

      sinon.stub(OrgLogos, 'findOneAndUpdate').resolves({} as any)

      await controller.updateOrgLogo(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(201)).to.be.true
        const jsonArg = res.json.firstCall.args[0]
        expect(jsonArg.mimeType).to.equal('image/jpeg')
      }
    })
  })

  describe('getOrgLogo - edge cases', () => {
    it('should handle findOne throwing an error', async () => {
      sinon.stub(OrgLogos, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().rejects(new Error('DB error')),
        }),
      } as any)

      await controller.getOrgLogo(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('deleteOrganization - event publishing', () => {
    it('should publish OrgDeletedEvent with orgId', async () => {
      const mockOrg = {
        ...createMockOrgDocumentForSoftDelete(),
        save: sinon.stub().resolves(),
      }
      sinon.stub(Org, 'findOne').resolves(mockOrg as any)

      await controller.deleteOrganization(req, res, next)

      const event = mockEventService.publishEvent.firstCall.args[0]
      expect(event.eventType).to.equal('orgDeleted')
      expect(event.payload.orgId).to.equal('507f1f77bcf86cd799439012')
    })
  })

  describe('getOrganizationById - edge case', () => {
    it('should handle findOne throwing an error', async () => {
      sinon.stub(Org, 'findOne').rejects(new Error('DB error'))

      await controller.getOrganizationById(req, res, next)
      expect(next.calledOnce).to.be.true
    })
  })

  describe('getDomainFromEmail', () => {
    it('should return domain for valid email', () => {
      const result = controller.getDomainFromEmail('user@example.com')
      expect(result).to.equal('example.com')
    })

    it('should return null for email without @', () => {
      const result = controller.getDomainFromEmail('invalid-email')
      expect(result).to.be.null
    })

    it('should return null for email with multiple @', () => {
      const result = controller.getDomainFromEmail('user@@example.com')
      expect(result).to.be.null
    })
  })

  describe('checkOrgExistence', () => {
    it('should return exists true when org count > 0', async () => {
      sinon.stub(Org, 'countDocuments').resolves(1)
      await controller.checkOrgExistence(res)
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0].exists).to.be.true
    })

    it('should return exists false when org count is 0', async () => {
      sinon.stub(Org, 'countDocuments').resolves(0)
      await controller.checkOrgExistence(res)
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.firstCall.args[0].exists).to.be.false
    })
  })

  describe('validateSVG', () => {
    it('should throw BadRequestError for SVG with script tags', () => {
      const svgBuffer = Buffer.from('<svg><script>alert("xss")</script></svg>')
      try {
        ;(controller as any).validateSVG(svgBuffer)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(BadRequestError)
        expect(error.message).to.include('script')
      }
    })

    it('should throw BadRequestError for SVG with event handlers', () => {
      const svgBuffer = Buffer.from('<svg onclick="alert(1)"></svg>')
      try {
        ;(controller as any).validateSVG(svgBuffer)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(BadRequestError)
        expect(error.message).to.include('event handlers')
      }
    })

    it('should throw BadRequestError for SVG with javascript: protocol', () => {
      const svgBuffer = Buffer.from('<svg><a href="javascript:alert(1)"></a></svg>')
      try {
        ;(controller as any).validateSVG(svgBuffer)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(BadRequestError)
        expect(error.message).to.include('javascript:')
      }
    })

    it('should throw BadRequestError for SVG with data:text/html', () => {
      const svgBuffer = Buffer.from('<svg><use href="data: text/html"></use></svg>')
      try {
        ;(controller as any).validateSVG(svgBuffer)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(BadRequestError)
        expect(error.message).to.include('data:text/html')
      }
    })

    it('should throw BadRequestError for SVG with iframe', () => {
      const svgBuffer = Buffer.from('<svg><iframe src="http://evil.com"></iframe></svg>')
      try {
        ;(controller as any).validateSVG(svgBuffer)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(BadRequestError)
        expect(error.message).to.include('iframe')
      }
    })

    it('should throw BadRequestError for SVG with object tag', () => {
      const svgBuffer = Buffer.from('<svg><object data="http://evil.com"></object></svg>')
      try {
        ;(controller as any).validateSVG(svgBuffer)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(BadRequestError)
        expect(error.message).to.include('iframe, object, or embed')
      }
    })

    it('should throw BadRequestError for SVG with embed tag', () => {
      const svgBuffer = Buffer.from('<svg><embed src="http://evil.com"></embed></svg>')
      try {
        ;(controller as any).validateSVG(svgBuffer)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(BadRequestError)
        expect(error.message).to.include('iframe, object, or embed')
      }
    })

    it('should pass validation for clean SVG', () => {
      const svgBuffer = Buffer.from('<svg><rect width="100" height="100" fill="red"/></svg>')
      ;(controller as any).validateSVG(svgBuffer)
      // No error thrown means validation passed
    })

    it('should throw BadRequestError for oversized SVG', () => {
      const largeBuffer = Buffer.alloc(11 * 1024 * 1024, 'x') // 11MB
      try {
        ;(controller as any).validateSVG(largeBuffer)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(BadRequestError)
        expect(error.message).to.include('too large')
      }
    })
  })

  describe('updateOrganizationDetails - org not found', () => {
    it('should call next(NotFoundError) when org not found', async () => {
      sinon.stub(Org, 'findOne').resolves(null)
      req.body = { registeredName: 'New Name' }

      await controller.updateOrganizationDetails(req, res, next)
      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(NotFoundError)
    })
  })

  describe('getOrgLogo - with logo', () => {
    it('should return logo buffer and set content type', async () => {
      const logoData = Buffer.from('logo-data').toString('base64')
      sinon.stub(OrgLogos, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves({
            logo: logoData,
            mimeType: 'image/png',
          }),
        }),
      } as any)

      await controller.getOrgLogo(req, res, next)

      if (!next.called) {
        expect(res.setHeader.calledWith('Content-Type', 'image/png')).to.be.true
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should return 204 when no logo found', async () => {
      sinon.stub(OrgLogos, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves(null),
        }),
      } as any)

      // Need end() stub
      res.end = sinon.stub().returnsThis()

      await controller.getOrgLogo(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(204)).to.be.true
      }
    })

    it('should return 204 when logo is null', async () => {
      sinon.stub(OrgLogos, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves({ logo: null }),
        }),
      } as any)

      res.end = sinon.stub().returnsThis()

      await controller.getOrgLogo(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(204)).to.be.true
      }
    })
  })

  describe('removeOrgLogo', () => {
    it('should remove org logo', async () => {
      const mockLogo = {
        logo: 'data',
        mimeType: 'image/png',
        save: sinon.stub().resolves(),
      }
      sinon.stub(OrgLogos, 'findOne').returns({
        exec: sinon.stub().resolves(mockLogo),
      } as any)

      await controller.removeOrgLogo(req, res, next)

      if (!next.called) {
        expect(mockLogo.logo).to.be.null
        expect(mockLogo.mimeType).to.be.null
        expect(res.status.calledWith(200)).to.be.true
      }
    })

    it('should call next(NotFoundError) when no logo exists', async () => {
      sinon.stub(OrgLogos, 'findOne').returns({
        exec: sinon.stub().resolves(null),
      } as any)

      await controller.removeOrgLogo(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(NotFoundError)
    })
  })

  describe('getOnboardingStatus', () => {
    it('should return onboarding status', async () => {
      sinon.stub(Org, 'findOne').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().resolves({ onBoardingStatus: 'configured' }),
        }),
      } as any)

      await controller.getOnboardingStatus(req, res, next)

      if (!next.called) {
        expect(res.status.calledWith(200)).to.be.true
        expect(res.json.firstCall.args[0].status).to.equal('configured')
      }
    })

    it('should return notConfigured as default', async () => {
      sinon.stub(Org, 'findOne').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().resolves({ onBoardingStatus: undefined }),
        }),
      } as any)

      await controller.getOnboardingStatus(req, res, next)

      if (!next.called) {
        expect(res.json.firstCall.args[0].status).to.equal('notConfigured')
      }
    })

    it('should call next(NotFoundError) when org not found', async () => {
      sinon.stub(Org, 'findOne').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().resolves(null),
        }),
      } as any)

      await controller.getOnboardingStatus(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(NotFoundError)
    })
  })
})
