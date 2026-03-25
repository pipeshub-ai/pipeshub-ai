import 'reflect-metadata';
import { expect } from 'chai';
import { mailConfigUrl, ORG_CREATED_ACTIVITY } from '../../../../src/modules/user_management/constants/constants';

describe('User Management Constants', () => {
  describe('mailConfigUrl', () => {
    it('should be a non-empty string', () => {
      expect(mailConfigUrl).to.be.a('string');
      expect(mailConfigUrl).to.not.be.empty;
    });

    it('should contain the expected API path for SMTP config', () => {
      expect(mailConfigUrl).to.equal('api/v1/configurationManager/smtpConfig');
    });

    it('should start with api/v1', () => {
      expect(mailConfigUrl).to.match(/^api\/v1\//);
    });
  });

  describe('ORG_CREATED_ACTIVITY', () => {
    it('should be a non-empty string', () => {
      expect(ORG_CREATED_ACTIVITY).to.be.a('string');
      expect(ORG_CREATED_ACTIVITY).to.not.be.empty;
    });

    it('should equal org_created', () => {
      expect(ORG_CREATED_ACTIVITY).to.equal('org_created');
    });
  });
});
