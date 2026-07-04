import { expect } from 'chai';
import {
  domainFromEmail,
  normalizeOrgId,
} from '../../../../src/libs/services/telemetry/identity';

describe('telemetry identity', () => {
  describe('domainFromEmail', () => {
    it('should extract the lower-cased domain from an email', () => {
      expect(domainFromEmail('user@Example.COM')).to.equal('example.com');
    });

    it('should use the last @ for addresses with quoted local parts', () => {
      expect(domainFromEmail('"weird@local"@domain.io')).to.equal('domain.io');
    });

    it('should trim whitespace around the domain', () => {
      expect(domainFromEmail('user@acme.io ')).to.equal('acme.io');
    });

    it('should return "unknown" for undefined, null, and empty values', () => {
      expect(domainFromEmail(undefined)).to.equal('unknown');
      expect(domainFromEmail(null)).to.equal('unknown');
      expect(domainFromEmail('')).to.equal('unknown');
    });

    it('should return "unknown" when there is no @ sign', () => {
      expect(domainFromEmail('not-an-email')).to.equal('unknown');
    });

    it('should return "unknown" for a trailing @ with no domain', () => {
      expect(domainFromEmail('user@')).to.equal('unknown');
    });
  });

  describe('normalizeOrgId', () => {
    it('should pass through a non-empty string', () => {
      expect(normalizeOrgId('org-42')).to.equal('org-42');
    });

    it('should stringify an ObjectId-like value', () => {
      const objectIdLike = { toString: () => '507f1f77bcf86cd799439011' };
      expect(normalizeOrgId(objectIdLike)).to.equal('507f1f77bcf86cd799439011');
    });

    it('should return "unknown" for null, undefined, and empty string', () => {
      expect(normalizeOrgId(null)).to.equal('unknown');
      expect(normalizeOrgId(undefined)).to.equal('unknown');
      expect(normalizeOrgId('')).to.equal('unknown');
    });

    it('should return "unknown" for a plain object', () => {
      expect(normalizeOrgId({ nested: true })).to.equal('unknown');
    });
  });
});
