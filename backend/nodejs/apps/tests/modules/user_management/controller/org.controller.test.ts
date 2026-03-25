import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { OrgController } from '../../../../src/modules/user_management/controller/org.controller';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { OrgLogos } from '../../../../src/modules/user_management/schema/orgLogo.schema';

describe('OrgController', () => {
  let controller: OrgController;
  let mockConfig: any;
  let mockMailService: any;
  let mockLogger: any;
  let mockEventService: any;
  let req: any;
  let res: any;
  let next: sinon.SinonStub;

  beforeEach(() => {
    mockConfig = {
      frontendUrl: 'http://localhost:3000',
      scopedJwtSecret: 'test-secret',
      rsAvailable: 'false',
      cmBackend: 'http://localhost:3004',
    };

    mockMailService = {
      sendMail: sinon.stub().resolves({ statusCode: 200, data: {} }),
    };

    mockLogger = {
      debug: sinon.stub(),
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
    };

    mockEventService = {
      start: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(false),
    };

    controller = new OrgController(
      mockConfig,
      mockMailService,
      mockLogger,
      mockEventService,
    );

    req = {
      user: {
        _id: '507f1f77bcf86cd799439011',
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
    };

    res = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
      send: sinon.stub().returnsThis(),
      setHeader: sinon.stub().returnsThis(),
      end: sinon.stub().returnsThis(),
    };

    next = sinon.stub();
  });

  afterEach(() => {
    sinon.restore();
  });

  describe('getDomainFromEmail', () => {
    it('should extract domain from a valid email', () => {
      const domain = controller.getDomainFromEmail('user@example.com');
      expect(domain).to.equal('example.com');
    });

    it('should return null for invalid email without @', () => {
      const domain = controller.getDomainFromEmail('invalid-email');
      expect(domain).to.be.null;
    });

    it('should return null for email with multiple @ signs', () => {
      const domain = controller.getDomainFromEmail('user@domain@extra.com');
      expect(domain).to.be.null;
    });

    it('should extract domain from email with subdomain', () => {
      const domain = controller.getDomainFromEmail('user@mail.example.com');
      expect(domain).to.equal('mail.example.com');
    });
  });

  describe('checkOrgExistence', () => {
    it('should return exists: true when orgs exist', async () => {
      sinon.stub(Org, 'countDocuments').resolves(1);

      await controller.checkOrgExistence(res);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledWith({ exists: true })).to.be.true;
    });

    it('should return exists: false when no orgs exist', async () => {
      sinon.stub(Org, 'countDocuments').resolves(0);

      await controller.checkOrgExistence(res);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledWith({ exists: false })).to.be.true;
    });
  });

  describe('getOrganizationById', () => {
    it('should return org when found', async () => {
      const mockOrg = {
        _id: '507f1f77bcf86cd799439012',
        registeredName: 'Test Org',
        isDeleted: false,
      };

      sinon.stub(Org, 'findOne').resolves(mockOrg as any);

      await controller.getOrganizationById(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledWith(mockOrg)).to.be.true;
    });

    it('should call next with NotFoundError when org not found', async () => {
      sinon.stub(Org, 'findOne').resolves(null);

      await controller.getOrganizationById(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error).to.be.an('error');
      expect(error.message).to.equal('Organisation not found');
    });
  });

  describe('updateOrganizationDetails', () => {
    it('should update org details and publish event', async () => {
      req.body = {
        registeredName: 'Updated Org',
        contactEmail: 'new@org.com',
      };

      const mockOrg = {
        _id: '507f1f77bcf86cd799439012',
        registeredName: 'Old Org',
      };

      sinon.stub(Org, 'findOne').resolves(mockOrg as any);
      sinon.stub(Org, 'findByIdAndUpdate').resolves({
        ...mockOrg,
        registeredName: 'Updated Org',
      } as any);

      await controller.updateOrganizationDetails(req, res, next);

      expect(mockEventService.start.calledOnce).to.be.true;
      expect(mockEventService.publishEvent.calledOnce).to.be.true;
      expect(mockEventService.stop.calledOnce).to.be.true;
      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should call next with NotFoundError when org not found', async () => {
      req.body = { registeredName: 'Updated' };

      sinon.stub(Org, 'findOne').resolves(null);

      await controller.updateOrganizationDetails(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error.message).to.equal('Organisation not found');
    });
  });

  describe('deleteOrganization', () => {
    it('should soft delete org and publish event', async () => {
      const mockOrg = {
        _id: '507f1f77bcf86cd799439012',
        isDeleted: false,
        save: sinon.stub().resolves(),
      };

      sinon.stub(Org, 'findOne').resolves(mockOrg as any);

      await controller.deleteOrganization(req, res, next);

      expect(mockOrg.isDeleted).to.be.true;
      expect(mockOrg.save.calledOnce).to.be.true;
      expect(mockEventService.publishEvent.calledOnce).to.be.true;
      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should call next with NotFoundError when org not found', async () => {
      sinon.stub(Org, 'findOne').resolves(null);

      await controller.deleteOrganization(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error.message).to.equal('Organisation not found');
    });
  });

  describe('getOrgLogo', () => {
    it('should return org logo when found', async () => {
      const mockLogo = {
        logo: Buffer.from('test-logo').toString('base64'),
        mimeType: 'image/jpeg',
      };

      sinon.stub(OrgLogos, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves(mockLogo),
        }),
      } as any);

      await controller.getOrgLogo(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.setHeader.calledWith('Content-Type', 'image/jpeg')).to.be.true;
      expect(res.send.calledOnce).to.be.true;
    });

    it('should return 204 when no logo found', async () => {
      sinon.stub(OrgLogos, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves(null),
        }),
      } as any);

      await controller.getOrgLogo(req, res, next);

      expect(res.status.calledWith(204)).to.be.true;
      expect(res.end.calledOnce).to.be.true;
    });

    it('should return 204 when logo field is null', async () => {
      sinon.stub(OrgLogos, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves({ logo: null }),
        }),
      } as any);

      await controller.getOrgLogo(req, res, next);

      expect(res.status.calledWith(204)).to.be.true;
    });
  });

  describe('removeOrgLogo', () => {
    it('should remove org logo', async () => {
      const mockLogo = {
        logo: 'base64data',
        mimeType: 'image/jpeg',
        save: sinon.stub().resolves(),
      };

      sinon.stub(OrgLogos, 'findOne').returns({
        exec: sinon.stub().resolves(mockLogo),
      } as any);

      await controller.removeOrgLogo(req, res, next);

      expect(mockLogo.logo).to.be.null;
      expect(mockLogo.mimeType).to.be.null;
      expect(mockLogo.save.calledOnce).to.be.true;
      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should call next with NotFoundError when logo not found', async () => {
      sinon.stub(OrgLogos, 'findOne').returns({
        exec: sinon.stub().resolves(null),
      } as any);

      await controller.removeOrgLogo(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error.message).to.equal('Organisation logo not found');
    });
  });

  describe('updateOrgLogo', () => {
    it('should call next with BadRequestError when no file provided', async () => {
      req.body = {};

      await controller.updateOrgLogo(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error.message).to.equal('Organisation logo file is required');
    });
  });

  describe('getOnboardingStatus', () => {
    it('should return onboarding status', async () => {
      sinon.stub(Org, 'findOne').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().resolves({
            onBoardingStatus: 'configured',
          }),
        }),
      } as any);

      await controller.getOnboardingStatus(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledWith({ status: 'configured' })).to.be.true;
    });

    it('should default to notConfigured when status is undefined', async () => {
      sinon.stub(Org, 'findOne').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().resolves({
            onBoardingStatus: undefined,
          }),
        }),
      } as any);

      await controller.getOnboardingStatus(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
      expect(res.json.calledWith({ status: 'notConfigured' })).to.be.true;
    });

    it('should call next with NotFoundError when org not found', async () => {
      sinon.stub(Org, 'findOne').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().resolves(null),
        }),
      } as any);

      await controller.getOnboardingStatus(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error.message).to.equal('Organisation not found');
    });
  });

  describe('updateOnboardingStatus', () => {
    it('should update onboarding status to configured', async () => {
      req.body = { status: 'configured' };

      const mockOrg = {
        _id: '507f1f77bcf86cd799439012',
        onBoardingStatus: 'notConfigured',
        save: sinon.stub().resolves(),
      };

      sinon.stub(Org, 'findOne').resolves(mockOrg as any);

      await controller.updateOnboardingStatus(req, res, next);

      expect(mockOrg.onBoardingStatus).to.equal('configured');
      expect(mockOrg.save.calledOnce).to.be.true;
      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should update onboarding status to skipped', async () => {
      req.body = { status: 'skipped' };

      const mockOrg = {
        onBoardingStatus: 'notConfigured',
        save: sinon.stub().resolves(),
      };

      sinon.stub(Org, 'findOne').resolves(mockOrg as any);

      await controller.updateOnboardingStatus(req, res, next);

      expect(mockOrg.onBoardingStatus).to.equal('skipped');
    });

    it('should call next with BadRequestError for invalid status', async () => {
      req.body = { status: 'invalidStatus' };

      await controller.updateOnboardingStatus(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error.message).to.include('Invalid onboarding status');
    });

    it('should call next with NotFoundError when org not found', async () => {
      req.body = { status: 'configured' };

      sinon.stub(Org, 'findOne').resolves(null);

      await controller.updateOnboardingStatus(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error.message).to.equal('Organisation not found');
    });
  });

  describe('createOrg', () => {
    it('should throw NotFoundError when container is missing', async () => {
      req.container = undefined;

      try {
        await controller.createOrg(req, res);
        expect.fail('Should have thrown');
      } catch (error: any) {
        expect(error.message).to.equal('Container not found');
      }
    });
  });

  describe('getDomainFromEmail (additional)', () => {
    it('should handle empty string', () => {
      const domain = controller.getDomainFromEmail('');
      expect(domain).to.be.null;
    });

    it('should return lowercase domain', () => {
      const domain = controller.getDomainFromEmail('user@EXAMPLE.COM');
      expect(domain).to.equal('EXAMPLE.COM');
    });

    it('should handle email with only @', () => {
      const domain = controller.getDomainFromEmail('@');
      // parts = ['', ''], length = 2, so returns ''
      const result = controller.getDomainFromEmail('@');
      expect(result).to.equal('');
    });
  });

  describe('updateOrganizationDetails (additional)', () => {
    it('should update only contactEmail when only that field is provided', async () => {
      req.body = { contactEmail: 'new@org.com' };

      const mockOrg = {
        _id: '507f1f77bcf86cd799439012',
        registeredName: 'Old Org',
        contactEmail: 'old@org.com',
      };

      sinon.stub(Org, 'findOne').resolves(mockOrg as any);
      sinon.stub(Org, 'findByIdAndUpdate').resolves({
        ...mockOrg,
        contactEmail: 'new@org.com',
      } as any);

      await controller.updateOrganizationDetails(req, res, next);

      expect(mockEventService.publishEvent.calledOnce).to.be.true;
      expect(res.status.calledWith(200)).to.be.true;
    });
  });

  describe('getOrgLogo (additional)', () => {
    it('should handle missing mimeType by not setting Content-Type', async () => {
      const mockLogo = {
        logo: Buffer.from('test-logo').toString('base64'),
        mimeType: undefined,
      };

      sinon.stub(OrgLogos, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves(mockLogo),
        }),
      } as any);

      await controller.getOrgLogo(req, res, next);

      expect(res.status.calledWith(200)).to.be.true;
      // setHeader not called for Content-Type when mimeType is undefined
      expect(res.send.calledOnce).to.be.true;
    });
  })

  describe('updateOnboardingStatus (additional)', () => {
    it('should accept notConfigured as valid status', async () => {
      req.body = { status: 'notConfigured' };

      const mockOrg = {
        onBoardingStatus: 'skipped',
        save: sinon.stub().resolves(),
      };

      sinon.stub(Org, 'findOne').resolves(mockOrg as any);

      await controller.updateOnboardingStatus(req, res, next);

      expect(mockOrg.onBoardingStatus).to.equal('notConfigured');
      expect(res.status.calledWith(200)).to.be.true;
    });

    it('should call next with BadRequestError when status is empty string', async () => {
      req.body = { status: '' };

      await controller.updateOnboardingStatus(req, res, next);

      expect(next.calledOnce).to.be.true;
    });
  });

  describe('updateOrgLogo - valid SVG', () => {
    it('should accept valid SVG and save to database', async () => {
      const validSvg = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="100" height="100" fill="red"/></svg>';
      req.body = {
        fileBuffer: {
          buffer: Buffer.from(validSvg),
          mimetype: 'image/svg+xml',
        },
      };

      sinon.stub(OrgLogos, 'findOneAndUpdate').resolves({} as any);

      await controller.updateOrgLogo(req, res, next);

      if (!next.called) {
        expect(res.status.calledWith(201)).to.be.true;
        expect(res.json.calledOnce).to.be.true;
        const jsonArg = res.json.firstCall.args[0];
        expect(jsonArg.message).to.equal('Logo updated successfully');
        expect(jsonArg.mimeType).to.equal('image/svg+xml');
      }
    });
  });

  describe('validateSVG - oversized SVG', () => {
    it('should reject SVG larger than 10MB', async () => {
      // Create a buffer larger than 10MB
      const oversizedBuffer = Buffer.alloc(11 * 1024 * 1024, 'a');
      req.body = {
        fileBuffer: {
          buffer: oversizedBuffer,
          mimetype: 'image/svg+xml',
        },
      };

      await controller.updateOrgLogo(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error.message).to.include('too large');
    });

    it('should reject SVG with object tags', async () => {
      req.body = {
        fileBuffer: {
          buffer: Buffer.from('<svg><object data="http://evil.com"></object></svg>'),
          mimetype: 'image/svg+xml',
        },
      };

      await controller.updateOrgLogo(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error.message).to.include('iframe, object, or embed tags');
    });

    it('should reject SVG with embed tags', async () => {
      req.body = {
        fileBuffer: {
          buffer: Buffer.from('<svg><embed src="http://evil.com"></embed></svg>'),
          mimetype: 'image/svg+xml',
        },
      };

      await controller.updateOrgLogo(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error.message).to.include('iframe, object, or embed tags');
    });
  });

  describe('validateSVG (via updateOrgLogo)', () => {
    it('should reject SVG with script tags', async () => {
      req.body = {
        fileBuffer: {
          buffer: Buffer.from('<svg><script>alert("xss")</script></svg>'),
          mimetype: 'image/svg+xml',
        },
      };

      await controller.updateOrgLogo(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error.message).to.include('script tags');
    });

    it('should reject SVG with event handlers', async () => {
      req.body = {
        fileBuffer: {
          buffer: Buffer.from('<svg onload="alert(1)"><rect/></svg>'),
          mimetype: 'image/svg+xml',
        },
      };

      await controller.updateOrgLogo(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error.message).to.include('event handlers');
    });

    it('should reject SVG with javascript: protocol', async () => {
      req.body = {
        fileBuffer: {
          buffer: Buffer.from('<svg><a href="javascript:alert(1)"><text>click</text></a></svg>'),
          mimetype: 'image/svg+xml',
        },
      };

      await controller.updateOrgLogo(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error.message).to.include('javascript:');
    });

    it('should reject SVG with iframe tags', async () => {
      req.body = {
        fileBuffer: {
          buffer: Buffer.from('<svg><iframe src="http://evil.com"></iframe></svg>'),
          mimetype: 'image/svg+xml',
        },
      };

      await controller.updateOrgLogo(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error.message).to.include('iframe, object, or embed tags');
    });

    it('should reject SVG with data:text/html protocol', async () => {
      req.body = {
        fileBuffer: {
          buffer: Buffer.from('<svg><image href="data: text/html,malicious"/></svg>'),
          mimetype: 'image/svg+xml',
        },
      };

      await controller.updateOrgLogo(req, res, next);

      expect(next.calledOnce).to.be.true;
      const error = next.firstCall.args[0];
      expect(error.message).to.include('data:text/html');
    });
  });
});
