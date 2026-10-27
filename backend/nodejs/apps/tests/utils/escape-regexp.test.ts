import { expect } from 'chai';
import { escapeRegExp } from '../../src/utils/escape-regexp';

describe('escapeRegExp', () => {
  // Every character the RegExp engine treats specially must be backslash-escaped
  // so a user's search string is matched literally (OT-9).
  const METACHARACTERS = ['.', '*', '+', '?', '^', '$', '{', '}', '(', ')', '|', '[', ']', '\\'];

  it('escapes each individual regex metacharacter', () => {
    for (const ch of METACHARACTERS) {
      expect(escapeRegExp(ch), `metacharacter ${ch}`).to.equal(`\\${ch}`);
    }
  });

  it('leaves metacharacter-free text untouched (no regression for normal search)', () => {
    for (const text of ['admin', 'John Doe', 'team-42', 'héllo', '日本語', 'user_name']) {
      expect(escapeRegExp(text)).to.equal(text);
    }
  });

  it('escapes the dot in an email so it matches literally', () => {
    expect(escapeRegExp('user@example.com')).to.equal('user@example\\.com');
    const re = new RegExp(escapeRegExp('user@example.com'), 'i');
    expect(re.test('user@example.com')).to.be.true;
    expect(re.test('user@exampleXcom')).to.be.false; // the dot is no longer a wildcard
  });

  it('escapes every metacharacter in a mixed string', () => {
    expect(escapeRegExp('a(b).*c')).to.equal('a\\(b\\)\\.\\*c');
    expect(escapeRegExp('^start$')).to.equal('\\^start\\$');
    expect(escapeRegExp('a+b?c|d')).to.equal('a\\+b\\?c\\|d');
    expect(escapeRegExp('x{1,3}[y]')).to.equal('x\\{1,3\\}\\[y\\]');
  });

  it('produces a RegExp that matches the input literally, not as a pattern', () => {
    for (const text of ['a(b', '.*', 'C++', 'a|b', '[admin]', 'who?', '$$$']) {
      const re = new RegExp(escapeRegExp(text));
      expect(re.test(text), `literal match of ${text}`).to.be.true;
    }
  });

  it('does not let ".*" match arbitrary text once escaped', () => {
    const re = new RegExp(escapeRegExp('.*'), 'i');
    expect(re.test('.*')).to.be.true; // the literal two characters
    expect(re.test('anything else')).to.be.false; // not a wildcard any more
  });

  it('never throws for inputs that are invalid regex when unescaped', () => {
    for (const text of ['(', '[', ')', '\\', '*', '+(', '[a-']) {
      expect(() => new RegExp(escapeRegExp(text)), `escaping ${JSON.stringify(text)}`).to.not.throw();
    }
  });

  it('handles the empty string', () => {
    expect(escapeRegExp('')).to.equal('');
  });
});
