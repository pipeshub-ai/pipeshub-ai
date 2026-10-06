import { expect } from 'chai';
import { fromCanonical, ROLE_TABLE, toCanonical } from '../../../src/modules/authz/domain/role-mapper';

describe('role mapper (PH03-02)', () => {
  for (const [type, entries] of Object.entries(ROLE_TABLE)) {
    for (const [stored, canonical] of entries) {
      it(`${type}:${stored} maps to ${canonical} and back to an equivalent value`, () => {
        expect(toCanonical(type, stored)).to.equal(canonical);
        const back = fromCanonical(type, canonical);
        expect(back).to.not.equal(null);
        expect(toCanonical(type, back)).to.equal(canonical);
      });
    }
  }

  it('maps ORGANIZER to manager and FILEORGANIZER to editor', () => {
    expect(toCanonical('kb', 'ORGANIZER')).to.equal('manager');
    expect(toCanonical('kb', 'FILEORGANIZER')).to.equal('editor');
    expect(fromCanonical('kb', 'editor')).to.equal('WRITER');
  });

  it('fails closed on unknown or missing values', () => {
    expect(toCanonical('kb', 'SUPERUSER')).to.equal('none');
    expect(toCanonical('kb', undefined)).to.equal('none');
    expect(toCanonical('chat', 'constructor')).to.equal('none');
    expect(toCanonical('document', 'OWNER')).to.equal('none');
  });

  it('has no resource mapping for team roles', () => {
    expect(toCanonical('team', 'OWNER')).to.equal('owner');
    expect(fromCanonical('project', 'manager')).to.equal(null);
    expect(toCanonical('project', 'OWNER')).to.equal('none');
  });
});
