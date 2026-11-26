import { expect } from 'chai';
import sinon from 'sinon';
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command';
import { sourceKey } from '../../../../src/modules/agent-harness/projection/citation-source';
import { RecordAccessUnavailableError } from '../../../../src/modules/agent-harness/ports/record-access';
import {
  HarnessArtifactReadAccess,
  HarnessRecordReadAccess,
  RECORD_ACCESS_BATCH,
} from '../../../../src/modules/enterprise_search/adapters/harness-record-access';
import { indexed, live } from '../../agent-harness/support/citations';

const query = {
  authorization: 'Bearer viewer-token',
  sources: [indexed('r1'), live('f1'), { kind: 'attachment' as const, recordId: 'r1' }, indexed('r2')],
};

describe('HarnessRecordReadAccess', () => {
  afterEach(() => sinon.restore());

  const respond = (statusCode: number, data: unknown = {}) =>
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode, data, msg: '' } as never);

  const failure = async (work: Promise<unknown>): Promise<RecordAccessUnavailableError> => {
    try {
      await work;
    } catch (error) {
      expect(error).to.be.instanceOf(RecordAccessUnavailableError);
      return error as RecordAccessUnavailableError;
    }
    return expect.fail('Expected the check to reject');
  };

  it('asks the query service once with the viewer credential and the distinct typed sources', async () => {
    const execute = respond(200, {
      readable: ['r2'],
      decisions: [
        { source: indexed('r1'), status: 'denied' },
        { source: live('f1'), status: 'unresolved' },
        { source: indexed('r2'), status: 'readable' },
      ],
    });
    const decided = await new HarnessRecordReadAccess(() => 'http://query:8000').decide(query);
    expect([...decided]).to.deep.equal([
      [sourceKey(indexed('r1')), 'denied'],
      [sourceKey(live('f1')), 'unresolved'],
      [sourceKey(indexed('r2')), 'readable'],
    ]);
    expect(execute.callCount).to.equal(1);
    const command = execute.firstCall.thisValue as unknown as {
      uri: string;
      headers: Record<string, string>;
      body: unknown;
    };
    expect(command.uri).to.equal('http://query:8000/api/v1/records/access');
    expect(command.headers.authorization).to.equal('Bearer viewer-token');
    expect(JSON.parse(String(command.body))).to.deep.equal({ references: [indexed('r1'), live('f1'), indexed('r2')] });
  });

  it('ignores an answer for a source it did not ask about', async () => {
    respond(200, {
      readable: ['r1', 'someone-elses'],
      decisions: [
        { source: indexed('r1'), status: 'readable' },
        { source: indexed('someone-elses'), status: 'readable' },
        { source: live('f-other'), status: 'readable' },
      ],
    });
    const decided = await new HarnessRecordReadAccess(() => 'http://q').decide(query);
    expect([...decided.keys()]).to.deep.equal([sourceKey(indexed('r1'))]);
  });

  it('splits more sources than one call takes into consecutive calls', async () => {
    const execute = respond(200, { readable: [], decisions: [] });
    const sources = Array.from({ length: RECORD_ACCESS_BATCH + 1 }, (_, i) => indexed(`r${String(i)}`));
    await new HarnessRecordReadAccess(() => 'http://q').decide({ ...query, sources });
    expect(execute.callCount).to.equal(2);
  });

  it('makes no call for nothing', async () => {
    const execute = respond(200, { readable: [], decisions: [] });
    expect((await new HarnessRecordReadAccess(() => 'http://q').decide({ ...query, sources: [] })).size).to.equal(0);
    expect(execute.callCount).to.equal(0);
  });

  for (const status of [401, 403]) {
    it(`reports ${String(status)} as refused`, async () => {
      respond(status);
      expect((await failure(new HarnessRecordReadAccess(() => 'http://q').decide(query))).reason).to.equal('refused');
    });
  }

  it('reports a server error, or a query service that predates typed sources (422), as unavailable', async () => {
    for (const status of [502, 422]) {
      sinon.restore();
      respond(status);
      expect((await failure(new HarnessRecordReadAccess(() => 'http://q').decide(query))).reason, String(status)).to.equal('unavailable');
    }
  });

  it('reports a transport failure as unavailable', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new TypeError('fetch failed'));
    expect((await failure(new HarnessRecordReadAccess(() => 'http://q').decide(query))).reason).to.equal('unavailable');
  });

  it('refuses a malformed answer instead of guessing', async () => {
    for (const data of [{ readable: 'everything' }, { readable: [] }, { readable: [], decisions: [{ source: indexed('r1'), status: 'maybe' }] }]) {
      sinon.restore();
      respond(200, data);
      expect((await failure(new HarnessRecordReadAccess(() => 'http://q').decide(query))).reason, JSON.stringify(data)).to.equal(
        'invalid_response',
      );
    }
  });
});

describe('HarnessArtifactReadAccess (SS0)', () => {
  afterEach(() => sinon.restore());

  const respond = (statusCode: number, data: unknown = {}) =>
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode, data, msg: '' } as never);

  it('AN-7: asks once with the viewer credential and the distinct artifact ids, and keeps only what it asked', async () => {
    const execute = respond(200, {
      readable: [],
      artifacts: [
        { artifactId: 'a1', status: 'readable' },
        { artifactId: 'a2', status: 'unresolved' },
        { artifactId: 'not-asked', status: 'readable' },
      ],
    });
    const decided = await new HarnessArtifactReadAccess(() => 'http://query:8000').decide({
      authorization: 'Bearer viewer-token',
      artifactIds: ['a1', 'a2', 'a1'],
    });
    expect([...decided]).to.deep.equal([
      ['a1', 'readable'],
      ['a2', 'unresolved'],
    ]);
    expect(execute.callCount).to.equal(1);
    const command = execute.firstCall.thisValue as unknown as { uri: string; headers: Record<string, string>; body: unknown };
    expect(command.uri).to.equal('http://query:8000/api/v1/records/access');
    expect(command.headers.authorization).to.equal('Bearer viewer-token');
    expect(JSON.parse(String(command.body))).to.deep.equal({ artifactIds: ['a1', 'a2'] });
  });

  for (const [name, statusCode, data, reason] of [
    ['an answer without artifacts', 200, { readable: [] }, 'invalid_response'],
    ['a malformed answer', 200, { artifacts: [{ artifactId: 'a1' }] }, 'invalid_response'],
    ['a refusal', 403, {}, 'refused'],
    ['an error', 500, {}, 'unavailable'],
  ] as const) {
    it(`AN-7: rejects on ${name}`, async () => {
      respond(statusCode, data);
      try {
        await new HarnessArtifactReadAccess(() => 'http://query:8000').decide({ authorization: 'Bearer v', artifactIds: ['a1'] });
      } catch (error) {
        expect(error).to.be.instanceOf(RecordAccessUnavailableError);
        expect((error as RecordAccessUnavailableError).reason).to.equal(reason);
        return;
      }
      expect.fail('Expected the check to reject');
    });
  }
});
