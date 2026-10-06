import { expect } from 'chai';
import { atLeast, CANONICAL_ROLES, maxRole, minRole, rank } from '../../../src/modules/authz/domain/ladder';

describe('canonical role ladder (PH03-01)', () => {
  it('ranks are total and strictly increasing', () => {
    const ranks = CANONICAL_ROLES.map(rank);
    expect(ranks).to.deep.equal([0, 1, 2, 3, 4, 5]);
  });

  it('none is the minimum and atLeast is reflexive', () => {
    for (const r of CANONICAL_ROLES) {
      expect(atLeast(r, r)).to.equal(true);
      expect(atLeast(r, 'none')).to.equal(true);
    }
    expect(atLeast('none', 'viewer')).to.equal(false);
  });

  it('maxRole and minRole are commutative; maxRole of nothing is none', () => {
    for (const a of CANONICAL_ROLES) {
      for (const b of CANONICAL_ROLES) {
        expect(maxRole(a, b)).to.equal(maxRole(b, a));
        expect(minRole(a, b)).to.equal(minRole(b, a));
        expect(atLeast(maxRole(a, b), a)).to.equal(true);
        expect(atLeast(a, minRole(a, b))).to.equal(true);
      }
    }
    expect(maxRole()).to.equal('none');
  });
});
