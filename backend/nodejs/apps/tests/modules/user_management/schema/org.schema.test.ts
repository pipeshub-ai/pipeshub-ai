import 'reflect-metadata';
import { expect } from 'chai';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';

describe('user_management/schema/org.schema', () => {
  it('should export the Org model backed by the "org" collection', () => {
    expect(Org).to.exist;
    expect(Org.modelName).to.equal('org');
    expect(Org.collection.name).to.equal('org');
  });

  describe('schema paths', () => {
    it('should have slug as a unique field', () => {
      const path: any = Org.schema.path('slug');
      expect(path).to.exist;
      expect(path.options.unique).to.be.true;
    });

    it('should have domain as required', () => {
      const path = Org.schema.path('domain');
      expect(path).to.have.property('isRequired', true);
    });

    it('should have contactEmail as required and lowercased', () => {
      const path: any = Org.schema.path('contactEmail');
      expect(path).to.have.property('isRequired', true);
      expect(path.options.lowercase).to.be.true;
    });

    it('should have accountType as required with an individual/business enum', () => {
      const path: any = Org.schema.path('accountType');
      expect(path).to.have.property('isRequired', true);
      expect(path.options.enum).to.deep.equal(['individual', 'business']);
    });

    it('should have onBoardingStatus with the expected enum values', () => {
      const path: any = Org.schema.path('onBoardingStatus');
      expect(path).to.exist;
      expect(path.options.enum).to.deep.equal([
        'configured',
        'notConfigured',
        'skipped',
      ]);
    });

    it('should have isDeleted default to false', () => {
      const path = Org.schema.path('isDeleted');
      expect(path.defaultValue).to.equal(false);
    });

    it('should have adminRoleGuardAt as a Date field', () => {
      const path = Org.schema.path('adminRoleGuardAt');
      expect(path).to.exist;
      expect(path.instance).to.equal('Date');
    });

    it('should have a nested permanentAddress with the expected fields', () => {
      expect(Org.schema.path('permanentAddress.addressLine1')).to.exist;
      expect(Org.schema.path('permanentAddress.city')).to.exist;
      expect(Org.schema.path('permanentAddress.state')).to.exist;
      expect(Org.schema.path('permanentAddress.postCode')).to.exist;
      expect(Org.schema.path('permanentAddress.country')).to.exist;
    });
  });

  describe('timestamps', () => {
    it('should have timestamps enabled', () => {
      expect(Org.schema.options.timestamps).to.equal(true);
    });
  });

  describe('validation', () => {
    const baseFields = {
      domain: 'example.com',
      contactEmail: 'admin@example.com',
    };

    it('should require domain', () => {
      const org = new Org({
        contactEmail: 'admin@example.com',
        accountType: 'individual',
      });
      const err = org.validateSync();
      expect(err?.errors.domain).to.exist;
    });

    it('should require contactEmail', () => {
      const org = new Org({ domain: 'example.com', accountType: 'individual' });
      const err = org.validateSync();
      expect(err?.errors.contactEmail).to.exist;
    });

    it('should require accountType', () => {
      const org = new Org({ ...baseFields });
      const err = org.validateSync();
      expect(err?.errors.accountType).to.exist;
    });

    it('should reject an accountType outside the enum', () => {
      const org = new Org({ ...baseFields, accountType: 'enterprise' as any });
      const err = org.validateSync();
      expect(err?.errors.accountType).to.exist;
    });

    it('should lowercase contactEmail', () => {
      const org = new Org({
        domain: 'example.com',
        contactEmail: 'ADMIN@EXAMPLE.COM',
        accountType: 'individual',
      });
      expect(org.contactEmail).to.equal('admin@example.com');
    });

    it('should default isDeleted to false', () => {
      const org = new Org({ ...baseFields, accountType: 'individual' });
      expect(org.isDeleted).to.equal(false);
    });

    it('should reject an onBoardingStatus outside the enum', () => {
      const org = new Org({
        ...baseFields,
        accountType: 'individual',
        onBoardingStatus: 'invalid-status' as any,
      });
      const err = org.validateSync();
      expect(err?.errors.onBoardingStatus).to.exist;
    });

    describe('registeredName conditional requirement', () => {
      // Mongoose only invokes a custom `validate` function when the path has
      // a defined value, so this validator catches an explicit empty string
      // but not an omitted field (that gap is closed upstream by the
      // OrgCreationBody Zod schema in org.routes.ts, which does require
      // registeredName for business accounts before an Org is constructed).
      it('should reject an empty registeredName for business accounts', () => {
        const org = new Org({
          ...baseFields,
          accountType: 'business',
          registeredName: '',
        });
        const err = org.validateSync();
        expect(err?.errors.registeredName).to.exist;
        expect(err?.errors.registeredName?.message).to.equal(
          'Registered Name is required for business accounts',
        );
      });

      it('should accept an empty registeredName for individual accounts', () => {
        const org = new Org({
          ...baseFields,
          accountType: 'individual',
          registeredName: '',
        });
        const err = org.validateSync();
        expect(err?.errors.registeredName).to.not.exist;
      });

      it('should not require registeredName when accountType is individual', () => {
        const org = new Org({ ...baseFields, accountType: 'individual' });
        const err = org.validateSync();
        expect(err?.errors.registeredName).to.not.exist;
      });

      it('should pass validation when registeredName is provided for a business account', () => {
        const org = new Org({
          ...baseFields,
          accountType: 'business',
          registeredName: 'Acme Corp',
        });
        const err = org.validateSync();
        expect(err?.errors.registeredName).to.not.exist;
      });
    });
  });
});
