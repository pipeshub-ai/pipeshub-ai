/**
 * The approval rules this build runs by: no org writes rules of its own, so a run asks by the built-in rules under
 * the operator's deployment-wide one (writes run, deletes and sends ask, code in the sandbox runs). A person answers
 * their own runs' asks in the conversation and the Inbox, and says whether an action of unknown outcome happened.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import { Container } from 'inversify';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import * as userAdminService from '../../../src/modules/user_management/services/user-admin.service';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { DeploymentApprovalRules } from '../../../src/modules/agent-harness/application/deployment-approval-rules';
import { ownRunsOnly } from '../../../src/modules/agent-harness/application/approval-queue';
import {
  APPROVALS_DOCUMENT_KEYS,
  approvalLayers,
  readApprovalsDocument,
} from '../../../src/modules/agent-harness/domain/approvals';
import { createAgentHarnessAdminRouter } from '../../../src/modules/agent-harness/transport/admin-routes';
import { bindAdminAuthorizer } from './support/admin-authorizer';
import { continueAs } from '../../helpers/request-org-context';

const ORG = 'org-1';

describe('the approval rules this build runs by', () => {
  it("the document holds the operator's rules; a member no schema holds makes it invalid", () => {
    expect(APPROVALS_DOCUMENT_KEYS.has('default')).to.equal(true);
    expect(APPROVALS_DOCUMENT_KEYS.has('agents')).to.equal(true);
    expect(readApprovalsDocument({ reviewerLists: { [ORG]: ['u-1'] } }).status).to.equal('invalid');
    const read = readApprovalsDocument({ default: { writes: 'ask' }, agents: { 'agent-a': { writes: 'deny' } } });
    expect(read.status === 'valid' && read.sections.editions).to.deep.equal({});
  });

  it('a run narrows through the deployment default, then its agent rule, unless an edition adds layers', () => {
    const read = readApprovalsDocument({ default: { writes: 'ask' }, agents: { 'agent-a': { tools: { 'jira.create_issue': 'deny' } } } });
    expect(approvalLayers(read, ORG, 'agent-a')).to.deep.equal([
      { classes: { write: 'ask', execute: 'ask' }, tools: {} },
      { classes: {}, tools: { 'jira.create_issue': 'deny' } },
    ]);
    const added = approvalLayers(read, ORG, 'agent-a', {
      org: () => [{ classes: { read: 'ask' } }],
      agent: () => [{ writes: 'deny' }],
    });
    expect(added.map((layer) => layer.classes)).to.deep.equal([
      { write: 'ask', execute: 'ask' },
      { read: 'ask' },
      {},
      { write: 'deny', execute: 'deny' },
    ]);
  });

  it("a chat's sandbox reports the deployment's rule and no org's own, whatever the document holds", async () => {
    const document = new InMemoryConfigDocumentStore<unknown>();
    await document.replace(null, {
      default: { tools: { run_code: 'ask' } },
      agents: { 'agent-a': { writes: 'deny' } },
    });
    expect(await new DeploymentApprovalRules(document).read(ORG)).to.deep.equal({
      policy: { org: null, agents: {} },
      deployment: { default: { tools: { run_code: 'ask' } } },
    });
  });

  it('nothing stored, or a document the schema refuses: no rule beyond the built-in ones', async () => {
    const none = new InMemoryConfigDocumentStore<unknown>();
    expect((await new DeploymentApprovalRules(none).read(ORG)).deployment.default).to.equal(null);
    const bad = new InMemoryConfigDocumentStore<unknown>();
    await bad.replace(null, { default: { writes: 'ask' }, reviewerLists: { [ORG]: ['u-1'] } });
    expect((await new DeploymentApprovalRules(bad).read(ORG)).deployment.default).to.equal(null);
  });

  it("the Inbox lists a viewer's own runs' asks only", async () => {
    expect(await ownRunsOnly.othersRuns({ orgId: ORG, actorId: 'u-admin' }, {} as never)).to.equal(false);
  });

  describe('the admin router serves no org approval rules and no console of other people’s unknown effects', () => {
    let server: Server;
    let base: string;

    before(() => {
      sinon.stub(userAdminService, 'isUserOrgAdmin').resolves(true);
      const container = new Container();
      container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
        authenticate: (req: any, _res: unknown, next: () => void) => {
          req.user = { userId: 'u-admin', orgId: ORG };
          req.tokenPayload = { userId: 'u-admin', orgId: ORG };
          continueAs(req.user, next);
        },
      } as never);
      const app = express();
      app.use(express.json());
      bindAdminAuthorizer(container);
      app.use('/api/v1/agent-harness/admin', createAgentHarnessAdminRouter(container));
      app.use(ErrorMiddleware.handleError());
      server = app.listen(0);
      base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/api/v1/agent-harness/admin`;
    });

    after(() => {
      sinon.restore();
      server.close();
    });

    for (const [method, path] of [
      ['GET', '/approvals'],
      ['PUT', '/approvals'],
      ['GET', '/unknown-effects'],
      ['POST', '/unknown-effects/run-1/ix.unknown.t1.0/resolution'],
    ] as const) {
      it(`${method} ${path} is not served (404)`, async () => {
        const response = await fetch(`${base}${path}`, {
          method,
          headers: { 'content-type': 'application/json' },
          ...(method === 'GET' ? {} : { body: '{}' }),
        });
        expect(response.status).to.equal(404);
      });
    }
  });
});
