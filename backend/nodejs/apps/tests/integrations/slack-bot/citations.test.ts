import 'reflect-metadata';
import { expect } from 'chai';
import {
  rewriteCitationsForSlack,
  stripFragmentDirective,
} from '../../../src/integrations/slack-bot/src/utils/citations';
import { markdownToSlackMrkdwn } from '../../../src/integrations/slack-bot/src/utils/md_to_mrkdwn';

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

describe('slack-bot citations: rewriteCitationsForSlack', () => {
  const wiki = 'https://en.wikipedia.org/wiki/Python_(programming_language)#:~:text=Python%202.0%20was%20released';
  const webCitation = { citationData: { chunkIndex: 1, metadata: { webUrl: wiki, connector: 'WEB' } } };
  const fileCitation = {
    citationData: { chunkIndex: 2, metadata: { recordId: 'abc-123', recordType: 'FILE', connector: 'CONFLUENCE' } },
  };

  it('replaces a citation whose URL has parentheses without leaving its tail in the text', () => {
    const out = rewriteCitationsForSlack(`Python 3.0 shipped in 2008 [1](${wiki}).`, [webCitation], 'https://app.test');
    expect(out).to.equal(`Python 3.0 shipped in 2008 [1](${wiki.replace(/\(/g, '%28').replace(/\)/g, '%29')}).`);
  });

  it('keeps the whole URL in the Slack link once converted to mrkdwn', () => {
    const md = rewriteCitationsForSlack(`Python 3.0 shipped in 2008 [1](${wiki}).`, [webCitation], 'https://app.test');
    const mrkdwn = markdownToSlackMrkdwn(md);
    const encoded = wiki.replace(/\(/g, '%28').replace(/\)/g, '%29');
    expect(mrkdwn).to.equal(`Python 3.0 shipped in 2008 <${encoded}|1>.`);
  });

  it('still maps a connector record citation to its record page', () => {
    const out = rewriteCitationsForSlack('Q3 grew [2](/record/abc-123/preview#blockIndex=4).', [fileCitation], 'https://app.test');
    expect(out).to.equal('Q3 grew [1](https://app.test/record/abc-123).');
  });
});
