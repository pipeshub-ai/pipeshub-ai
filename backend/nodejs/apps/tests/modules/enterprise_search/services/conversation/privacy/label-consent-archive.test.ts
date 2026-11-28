import { expect } from 'chai';
import { archivedLabelHistory } from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label';

const label = { v: 5, ver: 'v1', contentHash: 'h', direct: {}, parents: [], complete: true, tagset: 1, pub: false };

describe('a new version of an answer keeps the share of the version it replaces', () => {
  it('the share of the archived version, and whom it covered, go into the archive with its label', () => {
    const consent = { by: 'dana', at: new Date(0), ver: 'v1', scope: 'people' as const };
    const consentTo = [{ userId: 'bob', memberSince: 'user@1' }];
    const [entry] = archivedLabelHistory({ messageType: 'bot_response', labelVer: 'v1', provenance: label, consent, consentTo })!;
    expect(entry).to.deep.include({ ver: 'v1', consent, consentTo });
  });

  it('the agents it stays with and the shares it counts on are archived with it', () => {
    const consent = { by: 'dana', at: new Date(0), ver: 'v1', scope: 'everyone' as const, agents: ['X'], on: [{ row: 'r0', ver: 'v0' }, { row: 'copy', ver: '' }] };
    const [entry] = archivedLabelHistory({ messageType: 'bot_response', labelVer: 'v1', provenance: label, consent })!;
    expect(entry!.consent).to.deep.equal(consent);
  });

  it('a share of another version is not archived with this one', () => {
    const consent = { by: 'dana', at: new Date(0), ver: 'v0', scope: 'everyone' as const };
    const [entry] = archivedLabelHistory({ messageType: 'bot_response', labelVer: 'v1', provenance: label, consent })!;
    expect(entry).to.not.have.property('consent');
    expect(entry).to.not.have.property('consentTo');
  });

  it('an answer never shared archives as before', () => {
    const [entry] = archivedLabelHistory({ messageType: 'bot_response', labelVer: 'v1', provenance: label })!;
    expect(Object.keys(entry!).sort()).to.deep.equal(['provenance', 'ver']);
  });
});
