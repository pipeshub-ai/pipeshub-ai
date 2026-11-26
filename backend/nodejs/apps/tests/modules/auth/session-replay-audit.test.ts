import { expect } from 'chai';
import { getCurrentOrgId } from '../../../src/libs/context/orgContext';
import type { AuditEntry } from '../../../src/modules/agent-harness/domain/audit';
import {
  SESSION_REPLAYED_KIND,
} from '../../../src/modules/agent-harness/domain/session-replay-audit';
import { chainSessionReplayAudit } from '../../../src/modules/auth/session-replay-audit';

// R43AUTH-FU-1 (ADR-WEB-02 D9): an ended sign-in is on the org's audit chain, with ids only.
describe('a replayed refresh token on the audit chain', () => {
  it('is appended once, with the org bound, on the sign-in stream, naming no token or address', async () => {
    const appended: Array<{ orgId: string; entries: readonly AuditEntry[]; bound: string | undefined }> = [];
    const audit = chainSessionReplayAudit(
      () => ({
        append: async (orgId, entries) => {
          appended.push({ orgId, entries, bound: getCurrentOrgId() });
        },
      }),
      (work) => work({} as never),
      () => new Date('2026-11-14T10:00:00.000Z'),
    );
    await audit({ orgId: 'org-1', userId: 'u1', familyId: 'fam-1' });
    expect(appended).to.have.length(1);
    expect(appended[0]).to.deep.include({ orgId: 'org-1', bound: 'org-1' });
    const [entry] = appended[0]!.entries;
    expect(entry).to.deep.include({ kind: SESSION_REPLAYED_KIND, streamId: 'signin:fam-1', writer: 'node', sessionId: null });
    expect(entry!.fields).to.deep.equal({ account: 'p_u1', familyId: 'fam-1', actor: 'system', reason: 'refresh_reuse' });
  });
});
