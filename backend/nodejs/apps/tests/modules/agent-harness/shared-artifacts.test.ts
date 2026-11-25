import { expect } from 'chai';
import { InMemoryArtifactReadAccess } from '../../../src/modules/agent-harness/adapters/in-memory-artifact-access';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { SharedArtifactService } from '../../../src/modules/agent-harness/application/shared-artifacts';
import { CitationViewer } from '../../../src/modules/agent-harness/application/shared-citations';

type Line = [level: string, message: string, meta: Record<string, unknown>];

function recording(): { logger: HarnessLogger; lines: Line[] } {
  const lines: Line[] = [];
  const at = (level: string) => (message: string, meta: Record<string, unknown> = {}) => {
    lines.push([level, message, meta]);
  };
  return { lines, logger: { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') } };
}

const viewer: CitationViewer = {
  authorization: 'Bearer viewer',
  orgId: 'org-1',
  sessionId: 'session-1',
  runId: 'run-1',
  requestId: 'req-1',
  surface: 'stream',
};

describe('agent-harness: SharedArtifactService (SS0)', () => {
  it('AN-3: one batched check of the distinct ids with the viewer credential; counts only in the log', async () => {
    const artifacts = new InMemoryArtifactReadAccess().grant('Bearer viewer', 'a-open').deny('a-denied');
    const log = recording();
    const access = await new SharedArtifactService(artifacts, log.logger).decide(viewer, ['a-open', 'a-denied', 'a-unknown', 'a-open']);
    expect(artifacts.queries).to.deep.equal([{ authorization: 'Bearer viewer', artifactIds: ['a-open', 'a-denied', 'a-unknown'] }]);
    expect(access.shows('a-open')).to.equal(true);
    expect(access.shows('a-denied')).to.equal(false);
    expect(access.shows('a-unknown')).to.equal(false);
    expect(log.lines).to.have.length(1);
    const [level, event, meta] = log.lines[0]!;
    expect([level, event]).to.deep.equal(['info', 'artifacts.access_checked']);
    expect(meta).to.include({ artifacts: 3, allowed: 1, denied: 1, unverifiable: 1, orgId: 'org-1', runId: 'run-1', surface: 'stream' });
    expect(JSON.stringify(log.lines)).to.not.include('a-');
  });

  it('no artifacts: nothing asked, nothing logged', async () => {
    const artifacts = new InMemoryArtifactReadAccess();
    const log = recording();
    await new SharedArtifactService(artifacts, log.logger).decide(viewer, []);
    expect(artifacts.queries).to.deep.equal([]);
    expect(log.lines).to.deep.equal([]);
  });

  const failures: Array<{
    name: string;
    adapter: (a: InMemoryArtifactReadAccess) => InMemoryArtifactReadAccess | null;
    who: CitationViewer;
    reason: string;
  }> = [
    { name: 'no adapter', adapter: () => null, who: viewer, reason: 'not_configured' },
    { name: 'no credential', adapter: (a) => a, who: { ...viewer, authorization: undefined }, reason: 'no_credential' },
    { name: 'refusal', adapter: (a) => Object.assign(a, { failWith: 'refused' }), who: viewer, reason: 'refused' },
    { name: 'malformed answer', adapter: (a) => Object.assign(a, { failWith: 'invalid_response' }), who: viewer, reason: 'invalid_response' },
  ];
  for (const { name, adapter, who, reason } of failures) {
    it(`AN-4: fails closed on ${name}`, async () => {
      const log = recording();
      const service = new SharedArtifactService(adapter(new InMemoryArtifactReadAccess().grant('Bearer viewer', 'a-open')), log.logger);
      const access = await service.decide(who, ['a-open']);
      expect(access.shows('a-open')).to.equal(false);
      const [level, event, meta] = log.lines[0]!;
      expect([level, event]).to.deep.equal(['warn', 'artifacts.access_check_failed']);
      expect(meta).to.include({ reason, choice: 'hide_artifacts', artifacts: 1 });
    });
  }

  it('AN-4: an answer that leaves an artifact out hides it', async () => {
    const artifacts = new InMemoryArtifactReadAccess().grant('Bearer viewer', 'a-open');
    artifacts.dropAnswers = true;
    const log = recording();
    const access = await new SharedArtifactService(artifacts, log.logger).decide(viewer, ['a-open']);
    expect(access.shows('a-open')).to.equal(false);
    expect(log.lines[0]![2]).to.include({ allowed: 0, denied: 1 });
  });

  it('messagesFor: decides the artifacts the messages name and rewrites them', async () => {
    const artifacts = new InMemoryArtifactReadAccess();
    const messages = [{ content: '::artifact[s.csv](record:a-s){text/csv|d|a-s|SPREADSHEET|1}' }];
    const out = await new SharedArtifactService(artifacts).messagesFor(viewer, messages);
    expect(artifacts.queries[0]!.artifactIds).to.deep.equal(['a-s']);
    expect(out).to.deep.equal([{ content: '::artifact_unavailable' }]);
  });
});
