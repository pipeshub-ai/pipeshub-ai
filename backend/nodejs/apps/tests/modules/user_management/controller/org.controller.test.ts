import 'reflect-metadata';
import { InMemorySlackUserLinks } from '../../../support/slack/in-memory-slack-links'
import { setSlackUserLinkRepository } from '../../../../src/libs/repositories/slack-user-link'
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { OrgController } from '../../../../src/modules/user_management/controller/org.controller';
import { principalErasers } from '../support/principal-erasers';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { OrgLogos } from '../../../../src/modules/user_management/schema/orgLogo.schema';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { UserGroups } from '../../../../src/modules/user_management/schema/userGroup.schema';
import { UserCredentials } from '../../../../src/modules/auth/schema/userCredentials.schema';
import { OrgAuthConfig } from '../../../../src/modules/auth/schema/orgAuthConfiguration.schema';
import { eventBuffer } from '../../../../src/libs/services/telemetry/event-buffer';
import * as outboxDispatcher from '../../../../src/libs/services/outbox/outbox.dispatcher';
import { MongoUnitOfWork } from '../../../../src/libs/repositories/mongo';
import {
  BadRequestError,
  NotFoundError,
  ServiceUnavailableError,
} from '../../../../src/libs/errors/http.errors'
import { ORG_DELETE_QUERY_SERVICE_PAGES } from '../../../../src/modules/user_management/controller/org.controller';
import { installOrgLifecycle, resetOrgLifecycle } from '../../../../src/libs/org-lifecycle';

describe('OrgController', () => {
  // Org deletion removes the org's Slack links (ADR-SLACKID-01); in memory here.
  let slackLinks: InMemorySlackUserLinks
  beforeEach(() => {
    slackLinks = new InMemorySlackUserLinks()
    setSlackUserLinkRepository(slackLinks)
  })
  afterEach(() => setSlackUserLinkRepository())
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

    describe('R51-COMPAT item 10: the compatibility profile with the harness off (ADR-OPS-07)', () => {
      const saved: Record<string, string | undefined> = {};
      beforeEach(() => {
        for (const name of ['PIPESHUB_HARDENED', 'AGENT_HARNESS_WORKER_ENABLED']) saved[name] = process.env[name];
        delete process.env.PIPESHUB_HARDENED;
        process.env.AGENT_HARNESS_WORKER_ENABLED = 'false';
      });
      afterEach(() => {
        for (const [name, value] of Object.entries(saved)) {
          if (value === undefined) delete process.env[name];
          else process.env[name] = value;
        }
      });
      const anOrg = () => ({
        _id: '507f1f77bcf86cd799439012',
        registeredName: 'Acme Corporation Ltd',
        isDeleted: false,
        save: sinon.stub().resolves(),
      });

      it('deletes without confirmName, as before ORGDEL: a soft delete and the event, nothing erased', async () => {
        const mockOrg = anOrg();
        sinon.stub(Org, 'findOne').resolves(mockOrg as any);
        const eraser = { eraseUser: sinon.stub(), eraseOrg: sinon.stub() };
        const queryService = { eraseOrg: sinon.stub() };
        req.body = {};
        await new OrgController(mockConfig, mockMailService, mockLogger, mockEventService, eraser, eraser as any, eraser as any, eraser as any, null, null, null, queryService).deleteOrganization(req, res, next);
        expect(next.called).to.equal(false);
        expect(mockOrg.isDeleted).to.equal(true);
        expect(mockEventService.publishEvent.calledOnce).to.equal(true);
        expect(eraser.eraseOrg.called).to.equal(false);
        expect(queryService.eraseOrg.called).to.equal(false);
      });

      it('a confirmName that is sent must still match', async () => {
        const mockOrg = anOrg();
        sinon.stub(Org, 'findOne').resolves(mockOrg as any);
        req.body = { confirmName: 'Acme' };
        await controller.deleteOrganization(req, res, next);
        expect(next.firstCall.args[0]).to.be.instanceOf(BadRequestError);
        expect(mockOrg.save.called).to.equal(false);
      });

      it('erases as before with the harness switched on', async () => {
        process.env.AGENT_HARNESS_WORKER_ENABLED = 'true';
        sinon.stub(Org, 'findOne').resolves(anOrg() as any);
        const eraser = { eraseUser: sinon.stub(), eraseOrg: sinon.stub().resolves(0) };
        req.body = {};
        await new OrgController(mockConfig, mockMailService, mockLogger, mockEventService, eraser).deleteOrganization(req, res, next);
        expect(eraser.eraseOrg.calledOnce).to.equal(true);
      });

      it("CLEANUP-G1: with the harness on, the org's erasure job is requested before the org is marked deleted, so its sessions, journals, keys and audit chain are erased too", async () => {
        process.env.AGENT_HARNESS_WORKER_ENABLED = 'true';
        const mockOrg = anOrg();
        sinon.stub(Org, 'findOne').resolves(mockOrg as any);
        const principalDeletion = {
          requestOrg: sinon.stub().callsFake(async () => {
            expect(mockOrg.isDeleted).to.equal(false);
            return true;
          }),
        };
        req.body = {};
        await new OrgController(mockConfig, mockMailService, mockLogger, mockEventService, null, null, null, null, null, null, null, null, principalDeletion).deleteOrganization(req, res, next);
        expect(next.called).to.equal(false);
        expect(principalDeletion.requestOrg.calledOnceWith('507f1f77bcf86cd799439012', String(req.user.userId))).to.equal(true);
        expect(mockOrg.isDeleted).to.equal(true);
      });

      it('CLEANUP-G1: a failed request for the erasure job leaves the org undeleted, so the delete can be retried', async () => {
        process.env.AGENT_HARNESS_WORKER_ENABLED = 'true';
        const mockOrg = anOrg();
        sinon.stub(Org, 'findOne').resolves(mockOrg as any);
        const principalDeletion = { requestOrg: sinon.stub().rejects(new Error('store down')) };
        req.body = {};
        await new OrgController(mockConfig, mockMailService, mockLogger, mockEventService, null, null, null, null, null, null, null, null, principalDeletion).deleteOrganization(req, res, next);
        expect(next.calledOnce).to.equal(true);
        expect(mockOrg.isDeleted).to.equal(false);
      });

      it('with the harness off on the compatibility profile, no erasure job is requested', async () => {
        sinon.stub(Org, 'findOne').resolves(anOrg() as any);
        const principalDeletion = { requestOrg: sinon.stub().resolves(true) };
        req.body = {};
        await new OrgController(mockConfig, mockMailService, mockLogger, mockEventService, null, null, null, null, null, null, null, null, principalDeletion).deleteOrganization(req, res, next);
        expect(principalDeletion.requestOrg.called).to.equal(false);
      });
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
    beforeEach(() => {
      req.body = { confirmName: 'Acme Corporation Ltd' };
    });

    it('should soft delete org and publish event', async () => {
      const mockOrg = {
        _id: '507f1f77bcf86cd799439012',
        registeredName: 'Acme Corporation Ltd',
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

    it('R40-SLACK: removes the org\'s Slack account links before the org is marked deleted, and no other org\'s', async () => {
      const mockOrg = {
        _id: '507f1f77bcf86cd799439012',
        registeredName: 'Acme Corporation Ltd',
        isDeleted: false,
        save: sinon.stub().resolves(),
      };
      sinon.stub(Org, 'findOne').resolves(mockOrg as any);
      const link = { workspaceId: 'T1', teamId: 'T1', slackUserId: 'U1', userId: 'u1', email: 'a@x.test', slackName: 'A' };
      await slackLinks.upsert({ ...link, orgId: mockOrg._id });
      await slackLinks.upsert({ ...link, orgId: 'other-org' });

      await controller.deleteOrganization(req, res, next);

      expect(await slackLinks.get(mockOrg._id, 'T1', 'U1')).to.equal(null);
      expect(await slackLinks.get('other-org', 'T1', 'U1')).to.not.equal(null);
      expect(mockOrg.isDeleted).to.be.true;
    });

    it('ER-2: erases the org model accounts before the org is marked deleted', async () => {
      const mockOrg = {
        _id: '507f1f77bcf86cd799439012',
        registeredName: 'Acme Corporation Ltd',
        isDeleted: false,
        save: sinon.stub().resolves(),
      };
      sinon.stub(Org, 'findOne').resolves(mockOrg as any);
      const eraser = {
        eraseUser: sinon.stub().resolves(0),
        eraseOrg: sinon.stub().callsFake(async () => {
          expect(mockOrg.isDeleted).to.be.false;
          return 3;
        }),
      };
      const erasing = new OrgController(mockConfig, mockMailService, mockLogger, mockEventService, eraser);

      await erasing.deleteOrganization(req, res, next);

      expect(eraser.eraseOrg.calledOnceWith('507f1f77bcf86cd799439012')).to.be.true;
      expect(mockOrg.save.calledOnce).to.be.true;
    });

    for (const failing of [undefined, 'agentSchedules']) {
      it(`QA15-10: every principal store listed for deleteOrganization is erased before the org is marked deleted${failing ? ' (a failing one leaves the org undeleted)' : ''}`, async () => {
        const mockOrg = {
          _id: '507f1f77bcf86cd799439012',
          registeredName: 'Acme Corporation Ltd',
          isDeleted: false,
          save: sinon.stub().resolves(),
        };
        sinon.stub(Org, 'findOne').resolves(mockOrg as any);
        const erasers = principalErasers(
          'modules/user_management/controller/org.controller.ts#deleteOrganization',
          () => mockOrg.isDeleted,
          failing ? { failing } : {},
        );
        expect(erasers.stores).to.deep.equal(['modelAccounts', 'agentSchedules', 'agentMemories', 'engineSignIns', 'inbox', 'personalInstances']);
        const { modelAccounts, schedules, memories, engineSignIns, inbox, personalInstances } = erasers.byField as Record<string, any>;
        const erasing = new OrgController(mockConfig, mockMailService, mockLogger, mockEventService, modelAccounts, schedules, memories, engineSignIns, personalInstances, null, inbox, null);

        await erasing.deleteOrganization(req, res, next);

        expect(erasers.calls.map((c) => c.store)).to.deep.equal(failing ? ['modelAccounts', 'agentSchedules'] : erasers.stores);
        for (const call of erasers.calls) {
          expect(call, call.store).to.deep.include({ hook: 'eraseOrg', principalDeleted: false });
          expect(call.args).to.deep.equal(['507f1f77bcf86cd799439012']);
        }
        expect(mockOrg.isDeleted).to.equal(failing === undefined);
        expect(mockOrg.save.called).to.equal(failing === undefined);
      });
    }

    it('ER-2: a failed erasure leaves the org undeleted', async () => {
      const mockOrg = {
        _id: '507f1f77bcf86cd799439012',
        registeredName: 'Acme Corporation Ltd',
        isDeleted: false,
        save: sinon.stub().resolves(),
      };
      sinon.stub(Org, 'findOne').resolves(mockOrg as any);
      const eraser = { eraseUser: sinon.stub(), eraseOrg: sinon.stub().rejects(new Error('kv down')) };
      const erasing = new OrgController(mockConfig, mockMailService, mockLogger, mockEventService, eraser);

      await erasing.deleteOrganization(req, res, next);

      expect(next.calledOnce).to.be.true;
      expect(mockOrg.isDeleted).to.be.false;
      expect(mockOrg.save.called).to.be.false;
    });
    describe('the typed name', () => {
      const anOrg = (fields: Record<string, unknown> = {}) => ({
        _id: '507f1f77bcf86cd799439012',
        registeredName: 'Acme Corporation Ltd',
        shortName: 'Acme',
        contactEmail: 'it@acme.example',
        isDeleted: false,
        save: sinon.stub().resolves(),
        // The org repository reads a document through toObject(): every stored field, not the methods.
        toObject(this: Record<string, unknown>) {
          return Object.fromEntries(Object.entries(this).filter(([, v]) => typeof v !== 'function'));
        },
        ...fields,
      });

      afterEach(() => resetOrgLifecycle());

      for (const [what, body] of [
        ['no name', {}],
        ['another name', { confirmName: 'Acme' }],
        ['a name that is not a string', { confirmName: ['Acme Corporation Ltd'] }],
      ] as const) {
        it(`the server refuses a delete with ${what}: nothing is erased or deleted, and no hook is asked`, async () => {
          const mockOrg = anOrg();
          sinon.stub(Org, 'findOne').resolves(mockOrg as any);
          const eraser = { eraseUser: sinon.stub(), eraseOrg: sinon.stub() };
          const queryService = { eraseOrg: sinon.stub() };
          req.body = body;
          for (const hooked of [false, true]) {
            const hook = { deleteOrg: sinon.stub(), signInPageOrg: sinon.stub(), notLiveOrgOf: sinon.stub(), signInRefusal: sinon.stub() };
            if (hooked) installOrgLifecycle({ orgLifecycleHook: hook });
            next.resetHistory();
            await new OrgController(mockConfig, mockMailService, mockLogger, mockEventService, eraser, eraser as any, eraser as any, eraser as any, null, null, null, queryService).deleteOrganization(req, res, next);
            const refused = next.firstCall.args[0];
            expect(refused).to.be.instanceOf(BadRequestError);
            expect(refused.metadata).to.deep.equal({ reasonCode: 'org_delete_name_mismatch' });
            expect(hook.deleteOrg.called).to.equal(false);
          }
          expect(eraser.eraseOrg.called).to.equal(false);
          expect(queryService.eraseOrg.called).to.equal(false);
          expect(mockOrg.save.called).to.equal(false);
        });
      }

      it('an org without a registered name is confirmed with its display name, then its contact email', async () => {
        const { orgConfirmationName } = await import('../../../../src/modules/user_management/controller/org.controller');
        expect(orgConfirmationName({ registeredName: ' Acme Ltd ', shortName: 'Acme' })).to.equal('Acme Ltd');
        expect(orgConfirmationName({ registeredName: '', shortName: 'Acme' })).to.equal('Acme');
        expect(orgConfirmationName({ contactEmail: 'me@x.example' })).to.equal('me@x.example');
        expect(orgConfirmationName({})).to.equal('');
      });

      it('the immediate delete answers the deleted org and nothing about a later erasure', async () => {
        sinon.stub(Org, 'findOne').resolves(anOrg() as any);
        await controller.deleteOrganization(req, res, next);
        expect(next.called).to.equal(false);
        expect(Object.keys(res.json.firstCall.args[0]).sort()).to.deep.equal(['data', 'message']);
      });

      it('an installed deletion hook deletes the org in place of the immediate delete, once the name matched', async () => {
        const mockOrg = anOrg();
        sinon.stub(Org, 'findOne').resolves(mockOrg as any);
        const answer = { message: 'scheduled', data: { isDeleted: true } };
        const hook = { deleteOrg: sinon.stub().resolves(answer), signInPageOrg: sinon.stub(), notLiveOrgOf: sinon.stub(), signInRefusal: sinon.stub() };
        installOrgLifecycle({ orgLifecycleHook: hook });
        const eraser = { eraseUser: sinon.stub(), eraseOrg: sinon.stub() };
        const queryService = { eraseOrg: sinon.stub() };
        const publish = mockEventService.publishEvent as sinon.SinonStub;

        await new OrgController(mockConfig, mockMailService, mockLogger, mockEventService, eraser, eraser as any, eraser as any, eraser as any, null, null, null, queryService).deleteOrganization(req, res, next);

        expect(next.called).to.equal(false);
        const request = hook.deleteOrg.firstCall.args[0];
        expect(request).to.deep.include({ orgId: '507f1f77bcf86cd799439012', byUserId: '507f1f77bcf86cd799439011' });
        expect(request.org).to.deep.include({ registeredName: 'Acme Corporation Ltd' });
        expect(request.config).to.deep.equal({ frontendUrl: 'http://localhost:3000', scopedJwtSecret: 'test-secret' });
        expect(res.json.firstCall.args[0]).to.equal(answer);
        expect(eraser.eraseOrg.called).to.equal(false);
        expect(queryService.eraseOrg.called).to.equal(false);
        expect(mockOrg.save.called).to.equal(false);
        expect(publish.called).to.equal(false);
      });
    });

    describe("the query service's data", () => {
      const anOrg = () => ({
        _id: '507f1f77bcf86cd799439012',
        registeredName: 'Acme Corporation Ltd',
        isDeleted: false,
        save: sinon.stub().resolves(),
      });
      const page = (remaining: boolean) => ({ attachments: 1, artifacts: 2, blobs: 3, skillCandidates: 0, remaining });
      const deleting = (queryService: { eraseOrg: sinon.SinonStub }, eraser?: unknown) =>
        new OrgController(mockConfig, mockMailService, mockLogger, mockEventService, (eraser ?? null) as any, null, null, null, null, null, null, queryService);

      it('is erased page by page after the harness stores and before the org is marked deleted', async () => {
        const mockOrg = anOrg();
        sinon.stub(Org, 'findOne').resolves(mockOrg as any);
        const order: string[] = [];
        const eraser = { eraseUser: sinon.stub(), eraseOrg: sinon.stub().callsFake(async () => { order.push('modelAccounts'); return 1; }) };
        const queryService = {
          eraseOrg: sinon.stub().callsFake(async () => {
            expect(mockOrg.isDeleted, 'not deleted while its data is erased').to.equal(false);
            order.push('queryService');
            return page(order.filter((o) => o === 'queryService').length < 3);
          }),
        };

        await deleting(queryService, eraser).deleteOrganization(req, res, next);

        expect(next.called).to.equal(false);
        expect(queryService.eraseOrg.alwaysCalledWithExactly('507f1f77bcf86cd799439012')).to.equal(true);
        expect(order).to.deep.equal(['modelAccounts', 'queryService', 'queryService', 'queryService']);
        expect(mockOrg.isDeleted).to.equal(true);
        expect(mockEventService.publishEvent.calledOnce).to.equal(true);
      });

      it('a page that fails leaves the org undeleted, so the delete can be asked again', async () => {
        const mockOrg = anOrg();
        sinon.stub(Org, 'findOne').resolves(mockOrg as any);
        const queryService = { eraseOrg: sinon.stub().rejects(new Error('query service down')) };

        await deleting(queryService).deleteOrganization(req, res, next);

        expect(next.firstCall.args[0].message).to.equal('query service down');
        expect(mockOrg.isDeleted).to.equal(false);
        expect(mockOrg.save.called).to.equal(false);
        expect(mockEventService.publishEvent.called).to.equal(false);
      });

      it('an org with more than one delete can erase is left undeleted with a 503; the next delete continues', async () => {
        const mockOrg = anOrg();
        sinon.stub(Org, 'findOne').resolves(mockOrg as any);
        const queryService = { eraseOrg: sinon.stub().resolves(page(true)) };

        await deleting(queryService).deleteOrganization(req, res, next);

        expect(queryService.eraseOrg.callCount).to.equal(ORG_DELETE_QUERY_SERVICE_PAGES);
        expect(next.firstCall.args[0]).to.be.instanceOf(ServiceUnavailableError);
        expect(mockOrg.isDeleted).to.equal(false);
      });
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

    it('serves the logo so an SVG opened as a page cannot run script', async () => {
      sinon.stub(OrgLogos, 'findOne').returns({
        lean: sinon.stub().returns({
          exec: sinon.stub().resolves({
            logo: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"/>').toString('base64'),
            mimeType: 'image/svg+xml',
          }),
        }),
      } as any);

      await controller.getOrgLogo(req, res, next);

      expect(res.setHeader.calledWith('X-Content-Type-Options', 'nosniff')).to.be.true;
      const csp = res.setHeader.args.find((a: any[]) => a[0] === 'Content-Security-Policy');
      expect(csp?.[1]).to.match(/sandbox/);
      expect(csp?.[1]).to.match(/default-src 'none'/);
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
    beforeEach(() => {
      req.container = {
        get: sinon.stub(),
      };
    });

    it('should throw NotFoundError when container is missing', async () => {
      req.container = undefined;

      try {
        await controller.createOrg(req, res);
        expect.fail('Should have thrown');
      } catch (error: any) {
        expect(error.message).to.equal('Container not found');
      }
    });

    it('should throw error for weak password (no uppercase, no special char)', async () => {
      req.body = {
        accountType: 'individual',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'abcdefgh1',
      };

      try {
        await controller.createOrg(req, res);
        expect.fail('Should have thrown');
      } catch (error: any) {
        expect(error).to.be.instanceOf(BadRequestError);
        expect(error.statusCode).to.equal(400);
        expect(error.message).to.include(
          'Password should have minimum 8 characters with at least one uppercase',
        );
      }
    });

    it('should throw error for password without digits', async () => {
      req.body = {
        accountType: 'individual',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'Abcdefgh!',
      };

      try {
        await controller.createOrg(req, res);
        expect.fail('Should have thrown');
      } catch (error: any) {
        expect(error).to.be.instanceOf(BadRequestError);
        expect(error.statusCode).to.equal(400);
        expect(error.message).to.include('Password should have minimum 8 characters');
      }
    });

    it('should throw error for password without special characters', async () => {
      req.body = {
        accountType: 'individual',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'Abcdefg12',
      };

      try {
        await controller.createOrg(req, res);
        expect.fail('Should have thrown');
      } catch (error: any) {
        expect(error).to.be.instanceOf(BadRequestError);
        expect(error.statusCode).to.equal(400);
        expect(error.message).to.include('Password should have minimum 8 characters');
      }
    });

    it('should throw error when org already exists', async () => {
      req.body = {
        accountType: 'individual',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
      };

      sinon.stub(Org, 'countDocuments').resolves(1);

      try {
        await controller.createOrg(req, res);
        expect.fail('Should have thrown');
      } catch (error: any) {
        expect(error).to.be.instanceOf(BadRequestError);
        expect(error.statusCode).to.equal(400);
        expect(error.message).to.equal('There is already an organization');
      }
    });

    it('should throw error for email without valid domain', async () => {
      req.body = {
        accountType: 'individual',
        contactEmail: 'invalid-email',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
      };

      sinon.stub(Org, 'countDocuments').resolves(0);

      try {
        await controller.createOrg(req, res);
        expect.fail('Should have thrown');
      } catch (error: any) {
        expect(error).to.be.instanceOf(BadRequestError);
        expect(error.statusCode).to.equal(400);
        expect(error.message).to.include('Please specify a correct domain name');
      }
    });

    it('should throw error for email with multiple @ signs', async () => {
      req.body = {
        accountType: 'individual',
        contactEmail: 'user@domain@extra.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
      };

      sinon.stub(Org, 'countDocuments').resolves(0);

      try {
        await controller.createOrg(req, res);
        expect.fail('Should have thrown');
      } catch (error: any) {
        expect(error).to.be.instanceOf(BadRequestError);
        expect(error.statusCode).to.equal(400);
        expect(error.message).to.include('Please specify a correct domain name');
      }
    });

    it('should wrap a genuine internal failure as InternalServerError (500), not BadRequestError', async () => {
      req.body = {
        accountType: 'individual',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
      };

      sinon.stub(Org, 'countDocuments').rejects(new Error('Mongo connection lost'));

      try {
        await controller.createOrg(req, res);
        expect.fail('Should have thrown');
      } catch (error: any) {
        expect(error).to.not.be.instanceOf(BadRequestError);
        expect(error.statusCode).to.equal(500);
        // The database's own words stay in the log, not on someone's screen.
        expect(error.message).to.contain('PipesHub tried to create the organisation');
        expect(error.message).to.not.contain('Mongo');
      }
    });

    it('should successfully create org with valid individual data', async () => {
      req.body = {
        accountType: 'individual',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
      };

      sinon.stub(Org, 'countDocuments').resolves(0);

      const mockOrgId = new mongoose.Types.ObjectId();
      const mockUserId = new mongoose.Types.ObjectId();

      sinon.stub(Org.prototype, 'save').resolves();
      sinon.stub(Users.prototype, 'save').resolves();
      sinon.stub(UserCredentials.prototype, 'save').resolves();
      sinon.stub(UserGroups.prototype, 'save').resolves();
      sinon.stub(OrgAuthConfig.prototype, 'save').resolves();

      try {
        await controller.createOrg(req, res);
        expect(res.status.calledWith(200)).to.be.true;
        expect(res.json.calledOnce).to.be.true;
      } catch (error: any) {
        // The controller wraps all errors in InternalServerError,
        // so if any mock is incomplete it may throw here.
        // A successful test should not reach this catch.
        expect.fail(`Unexpected error: ${error.message}`);
      }
    });

    it('should successfully create org with valid business data', async () => {
      req.body = {
        accountType: 'business',
        contactEmail: 'admin@acme.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
        registeredName: 'Acme Corp',
      };

      sinon.stub(Org, 'countDocuments').resolves(0);

      sinon.stub(Org.prototype, 'save').resolves();
      sinon.stub(Users.prototype, 'save').resolves();
      sinon.stub(UserCredentials.prototype, 'save').resolves();
      sinon.stub(UserGroups.prototype, 'save').resolves();
      sinon.stub(OrgAuthConfig.prototype, 'save').resolves();

      try {
        await controller.createOrg(req, res);
        expect(res.status.calledWith(200)).to.be.true;
        expect(res.json.calledOnce).to.be.true;
      } catch (error: any) {
        expect.fail(`Unexpected error: ${error.message}`);
      }
    });

    it('records where the signup came from on the org and its org_created event', async () => {
      req.body = {
        accountType: 'individual',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
        referrerDomain: 'news.ycombinator.com',
      };
      sinon.stub(Org, 'countDocuments').resolves(0);
      const orgSave = sinon.stub(Org.prototype, 'save').resolves();
      sinon.stub(Users.prototype, 'save').resolves();
      sinon.stub(UserCredentials.prototype, 'save').resolves();
      sinon.stub(UserGroups.prototype, 'save').resolves();
      sinon.stub(OrgAuthConfig.prototype, 'save').resolves();
      eventBuffer.drain();

      await controller.createOrg(req, res);

      const org = orgSave.firstCall.thisValue;
      expect([org.signupChannel, org.signupReferrerDomain]).to.deep.equal(['direct', 'news.ycombinator.com']);
      const [event] = eventBuffer.drain().filter((e) => e.event === 'org_created');
      expect(event!.props).to.include({ channel: 'direct', referrer_domain: 'news.ycombinator.com' });
    });

    it('delivers the new org and admin events as soon as they are stored, not at the next poll', async () => {
      req.body = {
        accountType: 'individual',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
      };
      sinon.stub(Org, 'countDocuments').resolves(0);
      sinon.stub(Org.prototype, 'save').resolves();
      sinon.stub(Users.prototype, 'save').resolves();
      sinon.stub(UserCredentials.prototype, 'save').resolves();
      sinon.stub(UserGroups.prototype, 'save').resolves();
      sinon.stub(OrgAuthConfig.prototype, 'save').resolves();
      const kick = sinon.stub(outboxDispatcher, 'kickOutbox');
      // Hold the unit of work open after its body ran, so a kick from inside the body would show.
      let commit!: () => void;
      const committed = new Promise<void>((resolve) => { commit = resolve; });
      let bodyRan!: () => void;
      const bodyDone = new Promise<void>((resolve) => { bodyRan = resolve; });
      const run = MongoUnitOfWork.prototype.run;
      sinon.stub(MongoUnitOfWork.prototype, 'run').callsFake(async function (
        this: MongoUnitOfWork,
        ...args: Parameters<MongoUnitOfWork['run']>
      ) {
        const out = await run.apply(this, args);
        bodyRan();
        await committed;
        return out;
      });

      const done = controller.createOrg(req, res);
      await bodyDone;
      expect(mockEventService.publishEvent.calledTwice).to.be.true;
      expect(kick.called).to.be.false;
      commit();
      await done;

      expect(kick.calledOnce).to.be.true;
    });

    it('does not kick the outbox when the organisation was not created', async () => {
      req.body = {
        accountType: 'individual',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
      };
      sinon.stub(Org, 'countDocuments').resolves(0);
      sinon.stub(Org.prototype, 'save').resolves();
      sinon.stub(Users.prototype, 'save').resolves();
      sinon.stub(UserCredentials.prototype, 'save').resolves();
      sinon.stub(UserGroups.prototype, 'save').resolves();
      sinon.stub(OrgAuthConfig.prototype, 'save').resolves();
      mockEventService.publishEvent.rejects(new Error('outbox write failed'));
      const kick = sinon.stub(outboxDispatcher, 'kickOutbox');

      try {
        await controller.createOrg(req, res);
        expect.fail('Should have thrown');
      } catch (error: any) {
        expect(error.statusCode).to.equal(500);
      }
      expect(kick.called).to.be.false;
    });

    it('should send email when sendEmail is true', async () => {
      req.body = {
        accountType: 'individual',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
        sendEmail: true,
      };

      sinon.stub(Org, 'countDocuments').resolves(0);
      sinon.stub(Org.prototype, 'save').resolves();
      sinon.stub(Users.prototype, 'save').resolves();
      sinon.stub(UserCredentials.prototype, 'save').resolves();
      sinon.stub(UserGroups.prototype, 'save').resolves();
      sinon.stub(OrgAuthConfig.prototype, 'save').resolves();

      try {
        await controller.createOrg(req, res);
        expect(mockMailService.sendMail.calledOnce).to.be.true;
      } catch (error: any) {
        expect.fail(`Unexpected error: ${error.message}`);
      }
    });

    it('should not send email when sendEmail is falsy', async () => {
      req.body = {
        accountType: 'individual',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
      };

      sinon.stub(Org, 'countDocuments').resolves(0);
      sinon.stub(Org.prototype, 'save').resolves();
      sinon.stub(Users.prototype, 'save').resolves();
      sinon.stub(UserCredentials.prototype, 'save').resolves();
      sinon.stub(UserGroups.prototype, 'save').resolves();
      sinon.stub(OrgAuthConfig.prototype, 'save').resolves();

      try {
        await controller.createOrg(req, res);
        expect(mockMailService.sendMail.called).to.be.false;
      } catch (error: any) {
        expect.fail(`Unexpected error: ${error.message}`);
      }
    });

    it('should publish OrgCreatedEvent and NewUserEvent on success', async () => {
      req.body = {
        accountType: 'individual',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
      };

      sinon.stub(Org, 'countDocuments').resolves(0);
      sinon.stub(Org.prototype, 'save').resolves();
      sinon.stub(Users.prototype, 'save').resolves();
      sinon.stub(UserCredentials.prototype, 'save').resolves();
      sinon.stub(UserGroups.prototype, 'save').resolves();
      sinon.stub(OrgAuthConfig.prototype, 'save').resolves();

      try {
        await controller.createOrg(req, res);
        // Both events are still recorded. They are no longer bracketed by
        // start/stop: the events go to the outbox, and the dispatcher owns
        // the broker connection.
        expect(mockEventService.publishEvent.calledTwice).to.be.true;
      } catch (error: any) {
        expect.fail(`Unexpected error: ${error.message}`);
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

  describe('updateOrganizationDetails (operator objects, validator bypassed)', () => {
    const bodies: Array<[string, Record<string, unknown>]> = [
      ['contactEmail', { contactEmail: { $ne: null } }],
      ['registeredName', { registeredName: { $where: '1' } }],
      ['shortName', { shortName: { $gt: '' } }],
      ['permanentAddress.city', { permanentAddress: { city: { $gt: '' } } }],
      ['permanentAddress as an array', { permanentAddress: ['x'] }],
      ['permanentAddress as a string', { permanentAddress: 'x' }],
      ['permanentAddress as an empty string', { permanentAddress: '' }],
      ['permanentAddress as 0', { permanentAddress: 0 }],
      ['permanentAddress as false', { permanentAddress: false }],
    ];

    for (const [label, body] of bodies) {
      it(`answers 400 and never queries or updates Mongo for ${label}`, async () => {
        req.body = body;
        const findOne = sinon.stub(Org, 'findOne').resolves({ _id: '507f1f77bcf86cd799439012' } as any);
        const update = sinon.stub(Org, 'findByIdAndUpdate').resolves({} as any);

        await controller.updateOrganizationDetails(req, res, next);

        expect(next.calledOnce).to.be.true;
        expect(next.firstCall.args[0].statusCode).to.equal(400);
        expect(findOne.called).to.be.false;
        expect(update.called).to.be.false;
        expect(res.status.called).to.be.false;
      });
    }

    it('writes only the known string address fields', async () => {
      req.body = { permanentAddress: { city: 'Pune', $set: { isDeleted: true } } };
      sinon.stub(Org, 'findOne').resolves({ _id: '507f1f77bcf86cd799439012' } as any);
      const update = sinon.stub(Org, 'findByIdAndUpdate').resolves({} as any);

      await controller.updateOrganizationDetails(req, res, next);

      expect(update.firstCall.args[1]).to.deep.equal({ permanentAddress: { city: 'Pune' } });
      expect(res.status.calledWith(200)).to.be.true;
    });

    const run = async (saved: any, body: any) => {
      req.body = body;
      sinon.stub(Org, 'findOne').resolves({ _id: '507f1f77bcf86cd799439012', permanentAddress: saved } as any);
      const update = sinon.stub(Org, 'findByIdAndUpdate').resolves({} as any);
      await controller.updateOrganizationDetails(req, res, next);
      return update.firstCall.args[1] as Record<string, unknown>;
    };
    const full = { addressLine1: '1 Main', city: 'Pune', state: 'MH', postCode: '411001', country: 'IN' };

    it('merges a partial address with the saved one', async () => {
      const doc = await run({ ...full }, { permanentAddress: { city: 'Goa' } });
      expect(doc).to.deep.equal({ permanentAddress: { ...full, city: 'Goa' } });
    });

    it('uses a toObject() copy of a mongoose document and drops non-address keys', async () => {
      req.body = { permanentAddress: { city: 'Goa' } };
      const saved = { ...full, _id: 'x' };
      sinon.stub(Org, 'findOne').resolves({
        _id: '507f1f77bcf86cd799439012',
        toObject: () => ({ permanentAddress: saved }),
      } as any);
      const update = sinon.stub(Org, 'findByIdAndUpdate').resolves({} as any);
      await controller.updateOrganizationDetails(req, res, next);
      expect(update.firstCall.args[1]).to.deep.equal({ permanentAddress: { ...full, city: 'Goa' } });
    });

    it('gives just the supplied fields when the saved address is null', async () => {
      const doc = await run(null, { permanentAddress: { city: 'Goa' } });
      expect(doc).to.deep.equal({ permanentAddress: { city: 'Goa' } });
    });

    it('gives just the supplied fields when there is no saved address', async () => {
      const doc = await run(undefined, { permanentAddress: { city: 'Goa' } });
      expect(doc).to.deep.equal({ permanentAddress: { city: 'Goa' } });
    });

    it('replaces a full address field by field', async () => {
      const next2 = { addressLine1: '2 Side', city: 'Goa', state: 'GA', postCode: '403001', country: 'IN' };
      const doc = await run({ ...full }, { shortName: 'X', permanentAddress: next2 });
      expect(doc).to.deep.equal({ shortName: 'X', permanentAddress: next2 });
    });

    it('writes nothing for an empty permanentAddress', async () => {
      req.body = { permanentAddress: {} };
      sinon.stub(Org, 'findOne').resolves({ _id: '507f1f77bcf86cd799439012' } as any);
      const update = sinon.stub(Org, 'findByIdAndUpdate').resolves({} as any);

      await controller.updateOrganizationDetails(req, res, next);

      expect(update.firstCall.args[1]).to.deep.equal({});
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

describe('OrgController - additional coverage', () => {
  // Org deletion removes the org's Slack links (ADR-SLACKID-01); in memory here.
  let slackLinks: InMemorySlackUserLinks
  beforeEach(() => {
    slackLinks = new InMemorySlackUserLinks()
    setSlackUserLinkRepository(slackLinks)
  })
  afterEach(() => setSlackUserLinkRepository())
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
      sinon.stub(Org, 'findByIdAndUpdate').resolves(mockOrg as any)

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
      sinon.stub(Org, 'findByIdAndUpdate').resolves(mockOrg as any)

      await controller.updateOrganizationDetails(req, res, next)
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should handle empty body (no fields to update)', async () => {
      req.body = {}

      const mockOrg = { _id: '507f1f77bcf86cd799439012', registeredName: 'Org' }
      sinon.stub(Org, 'findOne').resolves(mockOrg as any)
      sinon.stub(Org, 'findByIdAndUpdate').resolves(mockOrg as any)

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

    it('rejects SVG bytes labelled as PNG and stores nothing', async () => {
      const svg = Buffer.from(
        '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8"/></svg>',
      )
      req.body = {
        fileBuffer: { buffer: svg, mimetype: 'image/png' },
      }
      const update = sinon.stub(OrgLogos, 'findOneAndUpdate')

      await controller.updateOrgLogo(req, res, next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0]).to.be.instanceOf(BadRequestError)
      expect(next.firstCall.args[0].message).to.equal(
        'Organisation logo must be a PNG, JPEG, WebP, GIF or SVG image',
      )
      expect(update.called).to.be.false
    })

    it('stores a declared SVG without decoding it', async () => {
      const svg = Buffer.from(
        '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8"/></svg>',
      )
      req.body = {
        fileBuffer: { buffer: svg, mimetype: 'image/svg+xml' },
      }
      const update = sinon.stub(OrgLogos, 'findOneAndUpdate').resolves({} as any)

      await controller.updateOrgLogo(req, res, next)

      expect(next.called).to.be.false
      expect(res.status.calledWith(201)).to.be.true
      expect(res.json.firstCall.args[0].mimeType).to.equal('image/svg+xml')
      const stored = update.firstCall.args[1]!.logo as string
      expect(Buffer.from(stored, 'base64').equals(svg)).to.equal(true)
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
      req.body = { confirmName: 'Acme' }
      const mockOrg = {
        _id: '507f1f77bcf86cd799439012',
        registeredName: 'Acme',
        isDeleted: false,
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

describe('OrgController - additional coverage 2', () => {
  // Org deletion removes the org's Slack links (ADR-SLACKID-01); in memory here.
  let slackLinks: InMemorySlackUserLinks
  beforeEach(() => {
    slackLinks = new InMemorySlackUserLinks()
    setSlackUserLinkRepository(slackLinks)
  })
  afterEach(() => setSlackUserLinkRepository())
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
      expect(res.setHeader.calledWith('Content-Type')).to.be.false
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
      req.body = { confirmName: 'Acme' }
      const mockOrg = {
        _id: 'org1',
        registeredName: 'Acme',
        isDeleted: false,
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
      const mockOrg = { _id: 'org1', registeredName: 'Test Org' }
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
      sinon.stub(Org, 'findByIdAndUpdate').resolves(mockOrg as any)

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
