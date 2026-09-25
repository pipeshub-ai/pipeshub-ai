import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { setDemoDataWorkspace } from '../../../../src/modules/knowledge_base/controllers/kb_controllers';
import { ConnectorServiceCommand } from '../../../../src/libs/commands/connector_service/connector.service.command';
import * as userAdmin from '../../../../src/modules/user_management/services/user-admin.service';
import * as demoAccounts from '../../../../src/modules/user_management/services/demo-accounts.service';
import { demoDataWorkspaceSchema } from '../../../../src/modules/knowledge_base/validators/validators';

const appConfig = { connectorBackend: 'http://localhost:8088' } as any;

function request(enabled: boolean): any {
  return {
    headers: { authorization: 'Bearer t' },
    body: { enabled },
    params: {},
    query: {},
    user: { userId: 'admin-1', orgId: 'org-1' },
  };
}

function response(): any {
  const res: any = { status: sinon.stub(), json: sinon.stub() };
  res.status.returns(res);
  return res;
}

function connector(
  ...responses: Array<{ statusCode: number; data?: unknown }>
) {
  const execute = sinon.stub(ConnectorServiceCommand.prototype, 'execute');
  responses.forEach((r, i) =>
    execute.onCall(i).resolves({ data: {}, ...r } as any),
  );
  return execute;
}

const body = (execute: sinon.SinonStub, call: number) =>
  JSON.parse((execute.getCall(call).thisValue as any).body);

describe('setDemoDataWorkspace', () => {
  beforeEach(() => sinon.stub(userAdmin, 'isUserOrgAdmin').resolves(true));
  afterEach(() => sinon.restore());

  it('turning it off stops the sample accounts first, then saves', async () => {
    const execute = connector(
      { statusCode: 200, data: { offForEveryone: false } },
      { statusCode: 200, data: { offForEveryone: true } },
    );
    const signIn = sinon
      .stub(demoAccounts, 'setSampleAccountsSignIn')
      .resolves(2);
    const next = sinon.stub();

    await setDemoDataWorkspace(appConfig)(request(false), response(), next);

    expect(next.called).to.equal(false);
    expect(signIn.calledOnceWith('org-1', 'admin-1', false)).to.equal(true);
    expect(signIn.firstCall.calledBefore(execute.getCall(1))).to.equal(true);
    expect(body(execute, 1)).to.deep.equal({ enabled: false });
  });

  it('saves nothing when the sample accounts cannot be stopped', async () => {
    const execute = connector({
      statusCode: 200,
      data: { offForEveryone: false },
    });
    sinon
      .stub(demoAccounts, 'setSampleAccountsSignIn')
      .rejects(new Error('mongo down'));
    const next = sinon.stub();

    await setDemoDataWorkspace(appConfig)(request(false), response(), next);

    expect(next.calledOnce).to.equal(true);
    expect(execute.callCount).to.equal(1); // the status read only; no workspace write
  });

  it('lets the accounts sign in again when the save fails and the demo stays on', async () => {
    connector(
      { statusCode: 200, data: { offForEveryone: false } },
      { statusCode: 403, data: {} },
    );
    const signIn = sinon
      .stub(demoAccounts, 'setSampleAccountsSignIn')
      .resolves(2);
    const next = sinon.stub();

    await setDemoDataWorkspace(appConfig)(request(false), response(), next);

    expect(next.calledOnce).to.equal(true);
    expect(signIn.getCalls().map((c) => c.args[2])).to.deep.equal([
      false,
      true,
    ]);
  });

  it('also lets the accounts back in when the save itself cannot be made', async () => {
    const execute = sinon.stub(ConnectorServiceCommand.prototype, 'execute');
    execute
      .onFirstCall()
      .resolves({ statusCode: 200, data: { offForEveryone: false } } as any);
    execute.onSecondCall().rejects(new Error('fetch failed'));
    execute
      .onThirdCall()
      .resolves({ statusCode: 200, data: { offForEveryone: false } } as any);
    const signIn = sinon
      .stub(demoAccounts, 'setSampleAccountsSignIn')
      .resolves(2);
    const next = sinon.stub();

    await setDemoDataWorkspace(appConfig)(request(false), response(), next);

    expect(next.calledOnce).to.equal(true);
    expect(signIn.getCalls().map((c) => c.args[2])).to.deep.equal([
      false,
      true,
    ]);
  });

  it('keeps the accounts stopped when the save went through but its reply was lost', async () => {
    const execute = sinon.stub(ConnectorServiceCommand.prototype, 'execute');
    execute
      .onFirstCall()
      .resolves({ statusCode: 200, data: { offForEveryone: false } } as any);
    execute
      .onSecondCall()
      .rejects(new SyntaxError('Unexpected end of JSON input'));
    execute
      .onThirdCall()
      .resolves({ statusCode: 200, data: { offForEveryone: true } } as any);
    const signIn = sinon
      .stub(demoAccounts, 'setSampleAccountsSignIn')
      .resolves(2);
    const next = sinon.stub();

    await setDemoDataWorkspace(appConfig)(request(false), response(), next);

    expect(next.calledOnce).to.equal(true);
    expect(signIn.getCalls().map((c) => c.args[2])).to.deep.equal([false]);
  });

  it('keeps the accounts stopped when it cannot tell whether the save went through', async () => {
    const execute = sinon.stub(ConnectorServiceCommand.prototype, 'execute');
    execute
      .onFirstCall()
      .resolves({ statusCode: 200, data: { offForEveryone: false } } as any);
    execute.onSecondCall().rejects(new Error('fetch failed'));
    execute.onThirdCall().rejects(new Error('fetch failed'));
    const signIn = sinon
      .stub(demoAccounts, 'setSampleAccountsSignIn')
      .resolves(2);
    const next = sinon.stub();

    await setDemoDataWorkspace(appConfig)(request(false), response(), next);

    expect(next.calledOnce).to.equal(true);
    expect(signIn.getCalls().map((c) => c.args[2])).to.deep.equal([false]);
  });

  it('turning it back on saves first, then lets the accounts sign in', async () => {
    const execute = connector(
      { statusCode: 200, data: { offForEveryone: true } },
      { statusCode: 200, data: { offForEveryone: false } },
    );
    const signIn = sinon
      .stub(demoAccounts, 'setSampleAccountsSignIn')
      .resolves(2);
    const next = sinon.stub();

    await setDemoDataWorkspace(appConfig)(request(true), response(), next);

    expect(next.called).to.equal(false);
    expect(body(execute, 1)).to.deep.equal({ enabled: true });
    expect(signIn.calledOnceWith('org-1', 'admin-1', true)).to.equal(true);
    expect(signIn.firstCall.calledAfter(execute.getCall(1))).to.equal(true);
  });

  it('leaves the accounts stopped when turning it back on is not saved', async () => {
    connector(
      { statusCode: 200, data: { offForEveryone: true } },
      { statusCode: 500, data: {} },
    );
    const signIn = sinon.stub(demoAccounts, 'setSampleAccountsSignIn');
    const next = sinon.stub();

    await setDemoDataWorkspace(appConfig)(request(true), response(), next);

    expect(next.calledOnce).to.equal(true);
    expect(signIn.called).to.equal(false);
  });

  it('changes nothing when the current setting cannot be read first', async () => {
    const execute = connector({ statusCode: 500 });
    const signIn = sinon.stub(demoAccounts, 'setSampleAccountsSignIn');
    const next = sinon.stub();

    await setDemoDataWorkspace(appConfig)(request(false), response(), next);

    expect(next.calledOnce).to.equal(true);
    expect(execute.calledOnce).to.equal(true);
    expect(signIn.called).to.equal(false);
  });
});

describe('setDemoDataWorkspace for a member', () => {
  afterEach(() => sinon.restore());

  it('refuses and changes nothing', async () => {
    sinon.stub(userAdmin, 'isUserOrgAdmin').resolves(false);
    const execute = sinon.stub(ConnectorServiceCommand.prototype, 'execute');
    const signIn = sinon.stub(demoAccounts, 'setSampleAccountsSignIn');
    const next = sinon.stub();

    await setDemoDataWorkspace(appConfig)(request(false), response(), next);

    expect(next.calledOnce).to.equal(true);
    expect(
      next.firstCall.args[0].statusCode ?? next.firstCall.args[0].status,
    ).to.equal(403);
    expect(execute.called).to.equal(false);
    expect(signIn.called).to.equal(false);
  });
});

describe('demoDataWorkspaceSchema', () => {
  it('takes one yes/no and nothing else', () => {
    expect(
      demoDataWorkspaceSchema.safeParse({ body: { enabled: false } }).success,
    ).to.equal(true);
    expect(demoDataWorkspaceSchema.safeParse({ body: {} }).success).to.equal(
      false,
    );
    expect(
      demoDataWorkspaceSchema.safeParse({
        body: { enabled: false, orgId: 'other' },
      }).success,
    ).to.equal(false);
  });
});
