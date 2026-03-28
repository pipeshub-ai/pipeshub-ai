import 'reflect-metadata';
import { expect } from 'chai';
import {
  samlSsoCallbackUrl,
  samlSsoConfigUrl,
} from '../../../../src/modules/auth/constants/constants';

describe('Auth Constants', () => {
  describe('samlSsoCallbackUrl', () => {
    it('should be defined and be a string', () => {
      expect(samlSsoCallbackUrl).to.be.a('string');
    });

    it('should have the correct SAML callback URL path', () => {
      expect(samlSsoCallbackUrl).to.equal('api/v1/saml/signIn/callback');
    });

    it('should start with api/v1/', () => {
      expect(samlSsoCallbackUrl).to.match(/^api\/v1\//);
    });
  });

  describe('samlSsoConfigUrl', () => {
    it('should be defined and be a string', () => {
      expect(samlSsoConfigUrl).to.be.a('string');
    });

    it('should have the correct SAML config URL path', () => {
      expect(samlSsoConfigUrl).to.equal(
        'api/v1/configurationManager/internal/authConfig/sso',
      );
    });

    it('should start with api/v1/', () => {
      expect(samlSsoConfigUrl).to.match(/^api\/v1\//);
    });

    it('should contain configurationManager in the path', () => {
      expect(samlSsoConfigUrl).to.include('configurationManager');
    });
  });
});
