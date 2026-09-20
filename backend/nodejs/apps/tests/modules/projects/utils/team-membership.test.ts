import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { resolveCallerTeamIds } from '../../../../src/modules/projects/utils/team-membership';
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command';
import { Logger } from '../../../../src/libs/services/logger.service';

const appConfig = { connectorBackend: 'http://localhost:8088' } as any;

/** Results are memoized per request object, so every test needs its own. */
function makeRequest(headers: Record<string, string> = { authorization: 'Bearer test-token' }): any {
  return { headers, user: { userId: 'u1', orgId: 'o1' } };
}

describe('resolveCallerTeamIds', () => {
  let warnStub: sinon.SinonStub;

  beforeEach(() => {
    warnStub = sinon.stub(Logger.prototype, 'warn');
  });

  afterEach(() => {
    sinon.restore();
  });

  it('returns the id of every team the caller belongs to', async () => {
    sinon
      .stub(AIServiceCommand.prototype, 'execute')
      .resolves({ statusCode: 200, data: { teams: [{ id: 'team-a' }, { id: 'team-b' }] } } as any);

    const teamIds = await resolveCallerTeamIds(makeRequest(), appConfig);

    expect(teamIds).to.deep.equal(['team-a', 'team-b']);
  });

  it('asks the connector service for a single generous page, as the caller', async () => {
    const executeStub = sinon
      .stub(AIServiceCommand.prototype, 'execute')
      .resolves({ statusCode: 200, data: { teams: [] } } as any);

    await resolveCallerTeamIds(makeRequest({ authorization: 'Bearer caller-token' }), appConfig);

    const command = executeStub.firstCall.thisValue as any;
    expect(command.uri).to.equal('http://localhost:8088/api/v1/entity/user/teams?limit=500');
    expect(command.method).to.equal('GET');
    expect(command.headers.authorization).to.equal('Bearer caller-token');
  });

  it('falls back to `_id` and drops rows that carry no id at all', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
      statusCode: 200,
      data: { teams: [{ id: 'team-a' }, { _id: 'team-b' }, {}, { id: '' }, { id: 'team-c', _id: 'ignored' }] },
    } as any);

    const teamIds = await resolveCallerTeamIds(makeRequest(), appConfig);

    expect(teamIds).to.deep.equal(['team-a', 'team-b', 'team-c']);
  });

  it('returns no teams when the response has no `teams` field', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as any);

    expect(await resolveCallerTeamIds(makeRequest(), appConfig)).to.deep.equal([]);
  });

  it('returns no teams when the response has no body', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: null } as any);

    expect(await resolveCallerTeamIds(makeRequest(), appConfig)).to.deep.equal([]);
  });

  it('returns no teams on a non-200, ignoring whatever the body claims', async () => {
    sinon
      .stub(AIServiceCommand.prototype, 'execute')
      .resolves({ statusCode: 403, data: { teams: [{ id: 'team-a' }] } } as any);

    expect(await resolveCallerTeamIds(makeRequest(), appConfig)).to.deep.equal([]);
  });

  it('degrades to no teams, with a warning, when the lookup throws', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('connect ECONNREFUSED'));

    const teamIds = await resolveCallerTeamIds(makeRequest(), appConfig);

    expect(teamIds).to.deep.equal([]);
    expect(warnStub.calledOnce).to.equal(true);
    expect(warnStub.firstCall.args[1]).to.deep.equal({ error: 'connect ECONNREFUSED' });
  });

  it('degrades the same way when something other than an Error is thrown', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(() => Promise.reject('upstream exploded'));

    const teamIds = await resolveCallerTeamIds(makeRequest(), appConfig);

    expect(teamIds).to.deep.equal([]);
    expect(warnStub.firstCall.args[1]).to.deep.equal({ error: 'upstream exploded' });
  });

  describe('per-request memoization', () => {
    it('issues one lookup for repeated calls on the same request', async () => {
      const executeStub = sinon
        .stub(AIServiceCommand.prototype, 'execute')
        .resolves({ statusCode: 200, data: { teams: [{ id: 'team-a' }] } } as any);
      const req = makeRequest();

      const first = await resolveCallerTeamIds(req, appConfig);
      const second = await resolveCallerTeamIds(req, appConfig);

      expect(executeStub.calledOnce).to.equal(true);
      expect(second).to.deep.equal(first);
    });

    it('shares the in-flight lookup between concurrent calls on the same request', async () => {
      const executeStub = sinon
        .stub(AIServiceCommand.prototype, 'execute')
        .resolves({ statusCode: 200, data: { teams: [{ id: 'team-a' }] } } as any);
      const req = makeRequest();

      const results = await Promise.all([
        resolveCallerTeamIds(req, appConfig),
        resolveCallerTeamIds(req, appConfig),
        resolveCallerTeamIds(req, appConfig),
      ]);

      expect(executeStub.calledOnce).to.equal(true);
      expect(results).to.deep.equal([['team-a'], ['team-a'], ['team-a']]);
    });

    it('never serves one request\'s teams to another request', async () => {
      const executeStub = sinon.stub(AIServiceCommand.prototype, 'execute');
      executeStub.onFirstCall().resolves({ statusCode: 200, data: { teams: [{ id: 'team-a' }] } } as any);
      executeStub.onSecondCall().resolves({ statusCode: 200, data: { teams: [{ id: 'team-b' }] } } as any);

      const forAlice = await resolveCallerTeamIds(makeRequest(), appConfig);
      const forBob = await resolveCallerTeamIds(makeRequest(), appConfig);

      expect(forAlice).to.deep.equal(['team-a']);
      expect(forBob).to.deep.equal(['team-b']);
      expect(executeStub.calledTwice).to.equal(true);
    });

    it('memoizes a failed lookup too, so one request does not retry it per project row', async () => {
      const executeStub = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('down'));
      const req = makeRequest();

      await resolveCallerTeamIds(req, appConfig);
      await resolveCallerTeamIds(req, appConfig);

      expect(executeStub.calledOnce).to.equal(true);
    });
  });
});
