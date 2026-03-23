import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  ConfigurationManagerService,
  GOOGLE_AUTH_CONFIG_PATH,
  AZURE_AD_AUTH_CONFIG_PATH,
  MICROSOFT_AUTH_CONFIG_PATH,
  OAUTH_AUTH_CONFIG_PATH,
  SSO_AUTH_CONFIG_PATH,
} from '../../../../src/modules/auth/services/cm.service';

describe('ConfigurationManagerService', () => {
  let cmService: ConfigurationManagerService;

  beforeEach(() => {
    cmService = new ConfigurationManagerService();
  });

  afterEach(() => {
    sinon.restore();
  });

  describe('exported constants', () => {
    it('should export GOOGLE_AUTH_CONFIG_PATH', () => {
      expect(GOOGLE_AUTH_CONFIG_PATH).to.equal(
        'api/v1/configurationManager/internal/authConfig/google',
      );
    });

    it('should export AZURE_AD_AUTH_CONFIG_PATH', () => {
      expect(AZURE_AD_AUTH_CONFIG_PATH).to.equal(
        'api/v1/configurationManager/internal/authConfig/azureAd',
      );
    });

    it('should export MICROSOFT_AUTH_CONFIG_PATH', () => {
      expect(MICROSOFT_AUTH_CONFIG_PATH).to.equal(
        'api/v1/configurationManager/internal/authConfig/microsoft',
      );
    });

    it('should export OAUTH_AUTH_CONFIG_PATH', () => {
      expect(OAUTH_AUTH_CONFIG_PATH).to.equal(
        'api/v1/configurationManager/internal/authConfig/oauth',
      );
    });

    it('should export SSO_AUTH_CONFIG_PATH', () => {
      expect(SSO_AUTH_CONFIG_PATH).to.equal(
        'api/v1/configurationManager/internal/authConfig/sso',
      );
    });
  });

  describe('constructor', () => {
    it('should create an instance', () => {
      expect(cmService).to.be.instanceOf(ConfigurationManagerService);
    });
  });

  describe('getConfig', () => {
    it('should be a function on the service', () => {
      expect(cmService.getConfig).to.be.a('function');
    });

    it('should accept four parameters', () => {
      // cmBackendUrl, configUrlPath, user, scopedJwtSecret
      expect(cmService.getConfig.length).to.equal(4);
    });
  });
});
