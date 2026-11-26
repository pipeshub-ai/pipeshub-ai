import { expect } from 'chai';
import { InMemoryRecordReadAccess } from '../../../src/modules/agent-harness/adapters/in-memory-record-access';
import {
  CitationViewer,
  SharedCitationService,
} from '../../../src/modules/agent-harness/application/shared-citations';
import {
  joinLabels,
  labelPasses,
  parseTurnLabel,
  TurnLabel,
} from '../../../src/modules/agent-harness/domain/turn-labels';
import { sourceKey } from '../../../src/modules/agent-harness/projection/citation-source';

/**
 * P2.13 LB-3: a turn built on published content names the publication in its
 * label; Node keeps that source apart from the bare record and asks the query
 * service about exactly it, with the viewer's own credential. The query
 * service answers readable only for the publication's audience (Python LB-2),
 * so a collaborator outside it gets the stub.
 */
const published = { kind: 'indexed' as const, recordId: 'r1', publicationId: 'p1' };
const bare = { kind: 'indexed' as const, recordId: 'r1' };
const label: TurnLabel = { complete: true, sources: [published] };

const viewer = (who: string): CitationViewer => ({
  authorization: `Bearer ${who}`,
  orgId: 'org-1',
  sessionId: 'session-1',
  requestId: 'req-1',
  surface: 'admission',
});

describe('agent-harness: labels that name a publication (P2.13)', () => {
  it('LB-3: a published source has its own key and survives parsing and joining', () => {
    expect(sourceKey(published)).to.not.equal(sourceKey(bare));
    expect(parseTurnLabel(label)).to.deep.equal(label);
    expect(joinLabels([label, { complete: true, sources: [bare] }]).sources).to.have.length(2);
    expect(parseTurnLabel({ complete: true, sources: [{ ...published, publicationId: '' }] })).to.equal(null);
  });

  it('LB-3: a status for the bare record never passes the published source', () => {
    const statuses = new Map([[sourceKey(bare), 'readable' as const]]);
    expect(labelPasses(label, statuses)).to.equal(false);
    expect(labelPasses(label, new Map([[sourceKey(published), 'readable' as const]]))).to.equal(true);
  });

  it('LB-3: the shared-session check asks about the publication and stubs a non-audience sender', async () => {
    // The in-memory authority stands for the query service: Alice is in p1's audience, Bob is not.
    const records = new InMemoryRecordReadAccess().grant('Bearer alice', 'r1');
    const service = new SharedCitationService(records);
    const turns = [{ id: 'run-alice', label }];
    expect([...(await service.passingTurns(viewer('alice'), turns))]).to.deep.equal(['run-alice']);
    expect([...(await service.passingTurns(viewer('bob'), turns))]).to.deep.equal([]);
    expect(records.queries.map((q) => q.sources)).to.deep.equal([[published], [published]]);
  });
});
