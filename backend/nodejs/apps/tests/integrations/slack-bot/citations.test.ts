import 'reflect-metadata';
import { expect } from 'chai';
import { stripFragmentDirective } from '../../../src/integrations/slack-bot/src/utils/citations';

describe('slack-bot citations: stripFragmentDirective', () => {
  it('returns urls without a directive unchanged', () => {
    expect(stripFragmentDirective('https://example.com/page')).to.equal('https://example.com/page');
    expect(stripFragmentDirective('https://example.com/page#section')).to.equal(
      'https://example.com/page#section',
    );
  });

  it('removes a bare directive and the dangling hash', () => {
    expect(stripFragmentDirective('https://example.com/page#:~:text=hello%20world,end')).to.equal(
      'https://example.com/page',
    );
  });

  it('keeps an anchor that precedes the directive', () => {
    expect(stripFragmentDirective('https://example.com/page#section:~:text=hello')).to.equal(
      'https://example.com/page#section',
    );
    expect(
      stripFragmentDirective('https://mail.google.com/mail?authuser=a@b.com#all/m1:~:text=Junior%20Process'),
    ).to.equal('https://mail.google.com/mail?authuser=a@b.com#all/m1');
  });

  it('drops everything after the first delimiter, including extra directives', () => {
    expect(stripFragmentDirective('https://example.com/p#a:~:text=x&text=y')).to.equal(
      'https://example.com/p#a',
    );
  });

  it('leaves the delimiter alone in a path or query', () => {
    expect(stripFragmentDirective('https://example.com/search?q=a:~:b')).to.equal(
      'https://example.com/search?q=a:~:b',
    );
    expect(stripFragmentDirective('https://example.com/search?q=a:~:b#:~:text=hello')).to.equal(
      'https://example.com/search?q=a:~:b',
    );
  });

  it('handles percent-encoded hyphens and commas in the directive', () => {
    expect(stripFragmentDirective('https://example.com/p#:~:text=e%2Dmail,Q3%2C%20done')).to.equal(
      'https://example.com/p',
    );
  });
});
