import { expect } from 'chai';
import {
  clientTags,
  normalizeOrgId,
  signupSource,
} from '../../../../src/libs/services/telemetry/identity';

describe('telemetry identity', () => {
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

  describe('clientTags', () => {
    it('keeps the first product token and a well-formed channel', () => {
      expect(
        clientTags({
          'user-agent': 'claude-code/2.1.0 (Linux; x64)',
          'x-pipeshub-channel': 'claude-code-plugin',
        }),
      ).to.deep.equal({
        user_agent: 'claude-code/2.1.0',
        channel: 'claude-code-plugin',
      });
    });

    it('drops a channel that is not a tag and a user agent that is an address', () => {
      expect(
        clientTags({
          'user-agent': 'me@example.com',
          'x-pipeshub-channel': 'Docs Site',
        }),
      ).to.deep.equal({});
    });
  });

  describe('signupSource', () => {
    it('records a well-formed ref as written', () => {
      expect(
        signupSource({ ref: 'docs', referrerDomain: 'google.com' }),
      ).to.deep.equal({
        channel: 'docs',
      });
    });

    it('falls back to direct with the referrer domain, lower-cased', () => {
      expect(
        signupSource({
          ref: 'Not A Tag',
          referrerDomain: 'News.YCombinator.com',
        }),
      ).to.deep.equal({
        channel: 'direct',
        referrerDomain: 'news.ycombinator.com',
      });
    });

    it('never keeps a referrer that is a URL rather than a domain', () => {
      expect(
        signupSource({ referrerDomain: 'https://example.com/private?q=1' }),
      ).to.deep.equal({
        channel: 'direct',
      });
      expect(signupSource({})).to.deep.equal({ channel: 'direct' });
    });
  });
});
