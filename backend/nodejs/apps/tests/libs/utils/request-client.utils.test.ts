import { expect } from 'chai';
import {
  emailDomain,
  requestClient,
  signupSource,
} from '../../../src/libs/utils/request-client.utils';

describe('request-client utils', () => {
  describe('emailDomain', () => {
    it('lower-cases the domain and answers unknown when there is none', () => {
      expect(emailDomain('user@Example.COM')).to.equal('example.com');
      expect(emailDomain('user@acme.io ')).to.equal('acme.io');
      expect(emailDomain(undefined)).to.equal('unknown');
      expect(emailDomain(null)).to.equal('unknown');
      expect(emailDomain('not-an-email')).to.equal('unknown');
      expect(emailDomain('user@')).to.equal('unknown');
    });
  });

  describe('requestClient', () => {
    it('keeps the first product token and a well-formed channel', () => {
      expect(
        requestClient({
          'user-agent': 'claude-code/2.1.0 (Linux; x64)',
          'x-pipeshub-channel': 'claude-code-plugin',
        }),
      ).to.deep.equal({ userAgent: 'claude-code/2.1.0', channel: 'claude-code-plugin' });
    });

    it('drops a channel that is not a tag and a user agent that is an address', () => {
      expect(
        requestClient({ 'user-agent': 'me@example.com', 'x-pipeshub-channel': 'Docs Site' }),
      ).to.deep.equal({});
    });
  });

  describe('signupSource', () => {
    it('records a well-formed ref as written', () => {
      expect(signupSource({ ref: 'docs', referrerDomain: 'google.com' })).to.deep.equal({
        channel: 'docs',
      });
    });

    it('falls back to direct with the referrer domain, lower-cased', () => {
      expect(signupSource({ ref: 'Not A Tag', referrerDomain: 'News.YCombinator.com' })).to.deep.equal({
        channel: 'direct',
        referrerDomain: 'news.ycombinator.com',
      });
    });

    it('never keeps a referrer that is a URL rather than a domain', () => {
      expect(signupSource({ referrerDomain: 'https://example.com/private?q=1' })).to.deep.equal({
        channel: 'direct',
      });
      expect(signupSource({})).to.deep.equal({ channel: 'direct' });
    });
  });
});
