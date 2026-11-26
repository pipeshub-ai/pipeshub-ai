/**
 * WP96-NODE (ADR-RUN-01 D3) on Node's side: `x.pipeshub.provenance.recorded`
 * is a journal shape Node takes (`provenance_records`), only from a worker,
 * and the audit chain keeps a result's readers, integrity, rule and a count
 * per source kind, never a URL, a repository path or a record id.
 */
import { expect } from 'chai';
import { AUDIT_EVENT_FIELDS } from '../../../src/modules/agent-harness/domain/audit';
import {
  NODE_CAPABILITIES,
  PROVENANCE_RECORDS_FEATURE,
} from '../../../src/modules/agent-harness/domain/node-capabilities';
import { pipeshubProvenanceRecordedEventSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/events/x-pipeshub-provenance-recorded';

const DATA = {
  invocationId: 'inv_1',
  toolId: 'pipeshub@git/repository_fetch',
  rule: 'repository',
  provenance: {
    readers: 'public',
    integrity: 'external',
    sources: [
      { kind: 'repository', url: 'https://github.com/onyx-dot-app/onyx', commit: '4f83564c1f0e8596be3f71d78f847a96447a3bdd' },
      { kind: 'web', url: 'https://onyx.app/', fetchedAt: '2026-11-05T12:00:00Z' },
      { kind: 'web', url: 'https://docs.onyx.app/' },
    ],
  },
};

const event = (writer: string) => ({
  orgId: 'org_1',
  sessionId: 'ses_1',
  streamId: 'ses_1',
  seq: 10,
  runId: 'run_1',
  schemaVersion: 1,
  critical: false,
  ts: '2026-11-05T12:00:00.123Z',
  visibility: 'owner',
  writer,
  type: 'x.pipeshub.provenance.recorded',
  data: DATA,
});

describe('WP96-NODE provenance records', () => {
  it('is a journal shape this Node names on its claims', () => {
    expect(NODE_CAPABILITIES.features).to.include(PROVENANCE_RECORDS_FEATURE);
    expect(PROVENANCE_RECORDS_FEATURE).to.equal('provenance_records');
  });

  it('is written by a worker only', () => {
    expect(pipeshubProvenanceRecordedEventSchema.safeParse(event('worker')).success).to.equal(true);
    expect(pipeshubProvenanceRecordedEventSchema.safeParse(event('node')).success).to.equal(false);
  });

  it('is audited by readers, integrity and source counts, never an address', () => {
    const fields = AUDIT_EVENT_FIELDS['x.pipeshub.provenance.recorded']!(DATA);
    expect(fields).to.deep.equal({
      invocationId: 'inv_1',
      toolId: 'pipeshub@git/repository_fetch',
      rule: 'repository',
      readers: 'public',
      integrity: 'external',
      sources: 3,
      repositorySources: 1,
      webSources: 2,
    });
    expect(JSON.stringify(fields)).not.to.contain('github.com/onyx');
  });
});
