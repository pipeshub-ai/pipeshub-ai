/**
 * Domain helpers that had no direct test: the egress proxy's refusal lines a person reads, the model-role guard
 * the policy reader trusts, and the erasure job ids both erasure stores key on.
 */
import { expect } from 'chai';
import { blockedSiteLine } from '../../../src/modules/agent-harness/domain/site-activity';
import { isModelRole, MODEL_ROLES } from '../../../src/modules/agent-harness/domain/model-roles';
import { orgErasureJobId, userErasureJobId } from '../../../src/modules/agent-harness/domain/principal-erasure';

describe('UI-ASIDE domain words and ids', () => {
  it('words every egress refusal by what happened, never by its code', () => {
    const lines: Record<string, string> = {
      destination_unresolvable: 'Could not reach example.com: the name does not resolve',
      tunnel_ip_literal: 'Blocked example.com: sites are reached by name, not address',
      tunnel_refused: 'Blocked example.com: this connection is not allowed',
      volume_cap_exceeded: 'Blocked example.com: the run moved more data than allowed',
      forbidden_pattern: 'Blocked a request to example.com that carried a credential',
      token_replayed: 'Blocked a request to example.com that carried a credential',
    };
    for (const [reason, label] of Object.entries(lines)) {
      const line = blockedSiteLine(reason, 'example.com');
      expect(line, reason).to.include({ kind: 'blocked', outcome: 'blocked', reason, label });
      expect(line?.label, reason).to.not.include(reason);
    }
  });

  it('a refusal with no host has no line; an unknown reason gets the plain one', () => {
    expect(blockedSiteLine('tunnel_refused', undefined)).to.equal(null);
    expect(blockedSiteLine('something_new', 'example.com')?.label).to.equal('Blocked a request to example.com');
  });

  it('a model role is only one of the listed roles', () => {
    for (const role of MODEL_ROLES) expect(isModelRole(role), role).to.equal(true);
    for (const value of ['', 'Primary', 'other', 1, null, undefined, {}]) expect(isModelRole(value)).to.equal(false);
  });

  it("erasure job ids keep an org's job apart from each person's", () => {
    expect(orgErasureJobId('o1')).to.equal('o1:org');
    expect(userErasureJobId('o1', 'u1')).to.equal('o1:u:u1');
    expect(userErasureJobId('o1', 'org')).to.not.equal(orgErasureJobId('o1'));
  });
});
