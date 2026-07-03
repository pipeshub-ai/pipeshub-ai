import { expect } from 'chai';
import { domainFromEmail } from '../../../../src/libs/services/telemetry/identity';

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
});
