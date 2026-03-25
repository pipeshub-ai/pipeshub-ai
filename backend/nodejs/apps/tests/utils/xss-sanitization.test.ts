import { expect } from 'chai';
import {
  containsXSSPattern,
  containsFormatSpecifiers,
  validateNoFormatSpecifiers,
  validateNoXSS,
  validateNoXSSOrFormatSpecifiers,
  sanitizeString,
  sanitizeForResponse,
  validateBooleanParam,
} from '../../src/utils/xss-sanitization';
import { BadRequestError } from '../../src/libs/errors/http.errors';

describe('xss-sanitization', () => {
  // -------------------------------------------------------------------------
  // containsXSSPattern
  // -------------------------------------------------------------------------
  describe('containsXSSPattern', () => {
    // --- Falsy / non-string inputs ---
    describe('when given null, undefined, or non-string values', () => {
      it('should return false for null', () => {
        expect(containsXSSPattern(null)).to.be.false;
      });

      it('should return false for undefined', () => {
        expect(containsXSSPattern(undefined)).to.be.false;
      });

      it('should return false for empty string', () => {
        expect(containsXSSPattern('')).to.be.false;
      });
    });

    // --- Safe inputs ---
    describe('when given safe inputs', () => {
      it('should return false for plain text', () => {
        expect(containsXSSPattern('hello world')).to.be.false;
      });

      it('should return false for text with numbers and punctuation', () => {
        expect(containsXSSPattern('Order #1234, total: $56.78')).to.be.false;
      });

      it('should return false for URLs without javascript protocol', () => {
        expect(containsXSSPattern('https://example.com/path?q=search')).to.be.false;
      });

      it('should return false for email addresses', () => {
        expect(containsXSSPattern('user@example.com')).to.be.false;
      });

      it('should return false for text containing "on" that is not an event handler', () => {
        expect(containsXSSPattern('I went on a trip')).to.be.false;
      });

      it('should return false for text containing "script" without angle brackets', () => {
        expect(containsXSSPattern('This is a script for the play')).to.be.false;
      });
    });

    // --- Script tags ---
    describe('when given script tags', () => {
      it('should detect basic script tag', () => {
        expect(containsXSSPattern('<script>alert("xss")</script>')).to.be.true;
      });

      it('should detect script tag with attributes', () => {
        expect(containsXSSPattern('<script type="text/javascript">alert(1)</script>')).to.be.true;
      });

      it('should detect script tag with mixed case', () => {
        expect(containsXSSPattern('<ScRiPt>alert(1)</ScRiPt>')).to.be.true;
      });

      it('should detect script tag with spaces', () => {
        expect(containsXSSPattern('< script >alert(1)</ script >')).to.be.true;
      });

      it('should detect opening script tag without closing', () => {
        expect(containsXSSPattern('<script>alert(1)')).to.be.true;
      });

      it('should detect closing script tag alone', () => {
        expect(containsXSSPattern('</script>')).to.be.true;
      });

      it('should detect closing script tag with spaces', () => {
        expect(containsXSSPattern('</script >')).to.be.true;
      });
    });

    // --- Iframe tags ---
    describe('when given iframe tags', () => {
      it('should detect iframe tag', () => {
        expect(containsXSSPattern('<iframe src="http://evil.com">')).to.be.true;
      });

      it('should detect iframe with mixed case', () => {
        expect(containsXSSPattern('<IFRAME src="http://evil.com">')).to.be.true;
      });

      it('should detect iframe with leading space', () => {
        expect(containsXSSPattern('< iframe src="evil">')).to.be.true;
      });
    });

    // --- Object tags ---
    describe('when given object tags', () => {
      it('should detect object tag', () => {
        expect(containsXSSPattern('<object data="evil.swf">')).to.be.true;
      });

      it('should detect object tag with mixed case', () => {
        expect(containsXSSPattern('<OBJECT data="evil.swf">')).to.be.true;
      });
    });

    // --- Embed tags ---
    describe('when given embed tags', () => {
      it('should detect embed tag', () => {
        expect(containsXSSPattern('<embed src="evil.swf">')).to.be.true;
      });

      it('should detect embed tag with mixed case', () => {
        expect(containsXSSPattern('<EMBED src="evil.swf">')).to.be.true;
      });
    });

    // --- SVG tags ---
    describe('when given svg tags', () => {
      it('should detect svg tag', () => {
        expect(containsXSSPattern('<svg onload="alert(1)">')).to.be.true;
      });

      it('should detect svg tag with mixed case', () => {
        expect(containsXSSPattern('<SVG onload="alert(1)">')).to.be.true;
      });
    });

    // --- Generic HTML tags ---
    describe('when given generic HTML tags', () => {
      it('should detect img tag', () => {
        expect(containsXSSPattern('<img src=x onerror=alert(1)>')).to.be.true;
      });

      it('should detect div tag', () => {
        expect(containsXSSPattern('<div>content</div>')).to.be.true;
      });

      it('should detect a tag', () => {
        expect(containsXSSPattern('<a href="evil">click</a>')).to.be.true;
      });
    });

    // --- Event handlers ---
    describe('when given event handlers', () => {
      it('should detect onerror handler', () => {
        expect(containsXSSPattern('onerror="alert(1)"')).to.be.true;
      });

      it('should detect onclick handler', () => {
        expect(containsXSSPattern('onclick="alert(1)"')).to.be.true;
      });

      it('should detect onload handler', () => {
        expect(containsXSSPattern('onload="alert(1)"')).to.be.true;
      });

      it('should detect onmouseover handler', () => {
        expect(containsXSSPattern('onmouseover="alert(1)"')).to.be.true;
      });

      it('should detect event handler with single quotes', () => {
        expect(containsXSSPattern("onfocus='alert(1)'")).to.be.true;
      });

      it('should detect event handler with space before equals', () => {
        expect(containsXSSPattern('onerror = "alert(1)"')).to.be.true;
      });
    });

    // --- JavaScript protocol ---
    describe('when given javascript: protocol', () => {
      it('should detect javascript: protocol', () => {
        expect(containsXSSPattern('javascript:alert(1)')).to.be.true;
      });

      it('should detect JavaScript: with mixed case', () => {
        expect(containsXSSPattern('JavaScript:alert(1)')).to.be.true;
      });

      it('should detect JAVASCRIPT: in uppercase', () => {
        expect(containsXSSPattern('JAVASCRIPT:alert(1)')).to.be.true;
      });
    });

    // --- Data protocol ---
    describe('when given data: protocol with text/html', () => {
      it('should detect data:text/html', () => {
        expect(containsXSSPattern('data:text/html,<script>alert(1)</script>')).to.be.true;
      });

      it('should detect data: text/html with space', () => {
        expect(containsXSSPattern('data: text/html,<script>alert(1)</script>')).to.be.true;
      });

      it('should detect data:text/html without script tags', () => {
        expect(containsXSSPattern('data:text/html,hello')).to.be.true;
      });
    });

    // --- JavaScript protocol standalone (no < present) ---
    describe('when given javascript: protocol without angle brackets', () => {
      it('should detect javascript:void(0)', () => {
        expect(containsXSSPattern('javascript:void(0)')).to.be.true;
      });
    });

    // --- Encoded patterns ---
    describe('when given encoded XSS patterns', () => {
      it('should detect HTML-entity encoded script: &lt;script', () => {
        expect(containsXSSPattern('&lt;script&gt;alert(1)&lt;/script&gt;')).to.be.true;
      });

      it('should detect URL-encoded script: %3cscript', () => {
        expect(containsXSSPattern('%3cscript%3ealert(1)%3c/script%3e')).to.be.true;
      });

      it('should detect decimal HTML entity &#60;script', () => {
        expect(containsXSSPattern('&#60;script>alert(1)</script>')).to.be.true;
      });

      it('should detect hex HTML entity &#x3c;script', () => {
        expect(containsXSSPattern('&#x3c;script>alert(1)</script>')).to.be.true;
      });
    });

    // --- Input length limit ---
    describe('when given extremely long input', () => {
      it('should return true for input longer than 100000 characters', () => {
        const longInput = 'a'.repeat(100001);
        expect(containsXSSPattern(longInput)).to.be.true;
      });

      it('should process input at exactly 100000 characters', () => {
        const exactInput = 'a'.repeat(100000);
        expect(containsXSSPattern(exactInput)).to.be.false;
      });
    });

    // --- Input truncation for regex ---
    describe('when given input longer than 10000 characters', () => {
      it('should detect XSS patterns in the first 10000 characters', () => {
        const payload = '<script>alert(1)</script>' + 'a'.repeat(10000);
        expect(containsXSSPattern(payload)).to.be.true;
      });

      it('should not detect XSS patterns beyond the first 10000 characters', () => {
        // The pattern is beyond the truncation point, but 'script' still appears in the full string
        // and '<' also appears in the full string. The regex check is done on the truncated value.
        const safePrefix = 'x'.repeat(10001);
        const payload = safePrefix + '<script>alert(1)</script>';
        // The string contains '<' and 'script' so it enters the regex branch,
        // but the regex only checks the first 10000 chars which are all 'x'
        expect(containsXSSPattern(payload)).to.be.false;
      });
    });
  });

  // -------------------------------------------------------------------------
  // containsFormatSpecifiers
  // -------------------------------------------------------------------------
  describe('containsFormatSpecifiers', () => {
    describe('when given null, undefined, or non-string values', () => {
      it('should return false for null', () => {
        expect(containsFormatSpecifiers(null)).to.be.false;
      });

      it('should return false for undefined', () => {
        expect(containsFormatSpecifiers(undefined)).to.be.false;
      });

      it('should return false for empty string', () => {
        expect(containsFormatSpecifiers('')).to.be.false;
      });
    });

    describe('when given strings without format specifiers', () => {
      it('should return false for plain text', () => {
        expect(containsFormatSpecifiers('hello world')).to.be.false;
      });

      it('should return false for a percent sign without specifier type', () => {
        expect(containsFormatSpecifiers('50%')).to.be.false;
      });

      it('should return false for text without %', () => {
        expect(containsFormatSpecifiers('no specifiers here')).to.be.false;
      });
    });

    describe('when given strings with format specifiers', () => {
      it('should detect %s', () => {
        expect(containsFormatSpecifiers('hello %s')).to.be.true;
      });

      it('should detect %d', () => {
        expect(containsFormatSpecifiers('count: %d')).to.be.true;
      });

      it('should detect %x', () => {
        expect(containsFormatSpecifiers('hex: %x')).to.be.true;
      });

      it('should detect %n (dangerous)', () => {
        expect(containsFormatSpecifiers('value: %n')).to.be.true;
      });

      it('should detect %f (float)', () => {
        expect(containsFormatSpecifiers('price: %f')).to.be.true;
      });

      it('should detect positional format specifier %1$s', () => {
        expect(containsFormatSpecifiers('name: %1$s')).to.be.true;
      });

      it('should detect format specifier with width %-10s', () => {
        expect(containsFormatSpecifiers('name: %-10s')).to.be.true;
      });

      it('should detect format specifier with precision %.2f', () => {
        expect(containsFormatSpecifiers('price: %.2f')).to.be.true;
      });

      it('should detect %% as a format specifier pattern', () => {
        // %% is technically an escaped percent, but the pattern matches it
        expect(containsFormatSpecifiers('100%%')).to.be.true;
      });
    });

    describe('when given extremely long input', () => {
      it('should return true for input longer than 100000 characters', () => {
        const longInput = 'a'.repeat(100001);
        expect(containsFormatSpecifiers(longInput)).to.be.true;
      });

      it('should process input at exactly 100000 characters', () => {
        const exactInput = 'a'.repeat(100000);
        expect(containsFormatSpecifiers(exactInput)).to.be.false;
      });
    });

    describe('when given input longer than 10000 characters', () => {
      it('should detect format specifiers in the first 10000 characters', () => {
        const payload = '%s' + 'a'.repeat(10000);
        expect(containsFormatSpecifiers(payload)).to.be.true;
      });
    });
  });

  // -------------------------------------------------------------------------
  // validateNoFormatSpecifiers
  // -------------------------------------------------------------------------
  describe('validateNoFormatSpecifiers', () => {
    it('should not throw for safe input', () => {
      expect(() => validateNoFormatSpecifiers('safe text')).to.not.throw();
    });

    it('should not throw for null', () => {
      expect(() => validateNoFormatSpecifiers(null)).to.not.throw();
    });

    it('should not throw for undefined', () => {
      expect(() => validateNoFormatSpecifiers(undefined)).to.not.throw();
    });

    it('should throw BadRequestError for format specifier %s', () => {
      expect(() => validateNoFormatSpecifiers('hello %s'))
        .to.throw(BadRequestError)
        .with.property('message')
        .that.includes('format specifiers');
    });

    it('should include fieldName in error message', () => {
      expect(() => validateNoFormatSpecifiers('hello %s', 'username'))
        .to.throw(BadRequestError)
        .with.property('message')
        .that.includes('username');
    });

    it('should use default fieldName "input" when not specified', () => {
      expect(() => validateNoFormatSpecifiers('hello %s'))
        .to.throw(BadRequestError)
        .with.property('message')
        .that.includes('input');
    });
  });

  // -------------------------------------------------------------------------
  // validateNoXSS
  // -------------------------------------------------------------------------
  describe('validateNoXSS', () => {
    it('should not throw for safe input', () => {
      expect(() => validateNoXSS('safe text')).to.not.throw();
    });

    it('should not throw for null', () => {
      expect(() => validateNoXSS(null)).to.not.throw();
    });

    it('should not throw for undefined', () => {
      expect(() => validateNoXSS(undefined)).to.not.throw();
    });

    it('should throw BadRequestError for script tag', () => {
      expect(() => validateNoXSS('<script>alert(1)</script>'))
        .to.throw(BadRequestError)
        .with.property('message')
        .that.includes('potentially dangerous content');
    });

    it('should include fieldName in error message', () => {
      expect(() => validateNoXSS('<script>alert(1)</script>', 'comment'))
        .to.throw(BadRequestError)
        .with.property('message')
        .that.includes('comment');
    });

    it('should use default fieldName "input" when not specified', () => {
      expect(() => validateNoXSS('<script>alert(1)</script>'))
        .to.throw(BadRequestError)
        .with.property('message')
        .that.includes('input');
    });
  });

  // -------------------------------------------------------------------------
  // validateNoXSSOrFormatSpecifiers
  // -------------------------------------------------------------------------
  describe('validateNoXSSOrFormatSpecifiers', () => {
    it('should not throw for safe input', () => {
      expect(() => validateNoXSSOrFormatSpecifiers('safe text')).to.not.throw();
    });

    it('should not throw for null', () => {
      expect(() => validateNoXSSOrFormatSpecifiers(null)).to.not.throw();
    });

    it('should not throw for undefined', () => {
      expect(() => validateNoXSSOrFormatSpecifiers(undefined)).to.not.throw();
    });

    it('should throw for XSS pattern', () => {
      expect(() => validateNoXSSOrFormatSpecifiers('<script>alert(1)</script>'))
        .to.throw(BadRequestError);
    });

    it('should throw for format specifier', () => {
      expect(() => validateNoXSSOrFormatSpecifiers('hello %s'))
        .to.throw(BadRequestError);
    });

    it('should include custom fieldName in the error for XSS', () => {
      expect(() => validateNoXSSOrFormatSpecifiers('<script>x</script>', 'title'))
        .to.throw(BadRequestError)
        .with.property('message')
        .that.includes('title');
    });

    it('should include custom fieldName in the error for format specifiers', () => {
      expect(() => validateNoXSSOrFormatSpecifiers('hello %s', 'title'))
        .to.throw(BadRequestError)
        .with.property('message')
        .that.includes('title');
    });
  });

  // -------------------------------------------------------------------------
  // sanitizeString
  // -------------------------------------------------------------------------
  describe('sanitizeString', () => {
    describe('when given null, undefined, or non-string values', () => {
      it('should return empty string for null', () => {
        expect(sanitizeString(null)).to.equal('');
      });

      it('should return empty string for undefined', () => {
        expect(sanitizeString(undefined)).to.equal('');
      });

      it('should return empty string for empty string', () => {
        expect(sanitizeString('')).to.equal('');
      });
    });

    describe('when given safe strings', () => {
      it('should encode special characters in plain text', () => {
        // sanitizeString always encodes &, <, >, ", ', /
        expect(sanitizeString('hello world')).to.equal('hello world');
      });

      it('should encode ampersand', () => {
        expect(sanitizeString('a & b')).to.equal('a &amp; b');
      });

      it('should encode less-than sign', () => {
        expect(sanitizeString('a < b')).to.equal('a &lt; b');
      });

      it('should encode greater-than sign', () => {
        expect(sanitizeString('a > b')).to.equal('a &gt; b');
      });

      it('should encode double quotes', () => {
        expect(sanitizeString('say "hello"')).to.equal('say &quot;hello&quot;');
      });

      it('should encode single quotes', () => {
        expect(sanitizeString("it's")).to.equal('it&#x27;s');
      });

      it('should encode forward slashes', () => {
        expect(sanitizeString('a/b')).to.equal('a&#x2F;b');
      });
    });

    describe('when given script tags', () => {
      it('should remove basic script tags', () => {
        const result = sanitizeString('<script>alert(1)</script>');
        expect(result).to.not.include('script');
        expect(result).to.not.include('<');
      });

      it('should remove script tags with mixed case', () => {
        const result = sanitizeString('<ScRiPt>alert(1)</ScRiPt>');
        expect(result).to.not.include('script');
        expect(result).to.not.include('Script');
      });

      it('should remove script tags with spaces', () => {
        const result = sanitizeString('< script >alert(1)</ script >');
        expect(result).to.not.include('script');
      });
    });

    describe('when given iframe tags', () => {
      it('should remove iframe tags', () => {
        const result = sanitizeString('<iframe src="http://evil.com"></iframe>');
        expect(result).to.not.include('iframe');
        expect(result).to.not.include('<');
      });

      it('should remove iframe tags with mixed case', () => {
        const result = sanitizeString('<IFRAME src="evil"></IFRAME>');
        expect(result).to.not.include('IFRAME');
        expect(result).to.not.include('iframe');
      });
    });

    describe('when given object tags', () => {
      it('should remove object tags', () => {
        const result = sanitizeString('<object data="evil.swf"></object>');
        expect(result).to.not.include('object');
      });
    });

    describe('when given embed tags', () => {
      it('should remove embed tags', () => {
        const result = sanitizeString('<embed src="evil.swf">');
        expect(result).to.not.include('embed');
      });
    });

    describe('when given svg tags', () => {
      it('should remove svg tags', () => {
        const result = sanitizeString('<svg onload="alert(1)"></svg>');
        expect(result).to.not.include('svg');
      });
    });

    describe('when given nested/recursive XSS patterns', () => {
      it('should handle nested script tags: <scr<script>ipt>', () => {
        const result = sanitizeString('<scr<script>ipt>alert(1)</script>');
        expect(result).to.not.include('script');
        expect(result).to.not.include('<');
      });

      it('should handle double-nested patterns', () => {
        const result = sanitizeString('<scr<scr<script>ipt>ipt>alert(1)');
        expect(result).to.not.include('script');
        expect(result).to.not.include('<');
      });
    });

    describe('when given other HTML tags', () => {
      it('should remove generic HTML tags like <div>', () => {
        const result = sanitizeString('<div>content</div>');
        expect(result).to.not.include('<');
        expect(result).to.include('content');
      });

      it('should remove img tags', () => {
        const result = sanitizeString('<img src=x onerror=alert(1)>');
        expect(result).to.not.include('<');
        expect(result).to.not.include('img');
      });
    });

    describe('when given extremely long input', () => {
      it('should truncate input longer than 100000 characters', () => {
        const longInput = 'a'.repeat(150000);
        const result = sanitizeString(longInput);
        expect(result.length).to.be.at.most(100000);
      });
    });
  });

  // -------------------------------------------------------------------------
  // sanitizeForResponse
  // -------------------------------------------------------------------------
  describe('sanitizeForResponse', () => {
    describe('when given null and undefined', () => {
      it('should return null for null', () => {
        expect(sanitizeForResponse(null)).to.be.null;
      });

      it('should return undefined for undefined', () => {
        expect(sanitizeForResponse(undefined)).to.be.undefined;
      });
    });

    describe('when given primitive types', () => {
      it('should return numbers as-is', () => {
        expect(sanitizeForResponse(42)).to.equal(42);
      });

      it('should return booleans as-is', () => {
        expect(sanitizeForResponse(true)).to.be.true;
        expect(sanitizeForResponse(false)).to.be.false;
      });

      it('should sanitize strings', () => {
        const result = sanitizeForResponse('<script>alert(1)</script>');
        expect(result).to.not.include('script');
        expect(result).to.not.include('<');
      });

      it('should return safe strings with encoded characters', () => {
        expect(sanitizeForResponse('hello')).to.equal('hello');
      });
    });

    describe('when given arrays', () => {
      it('should sanitize each string element in an array', () => {
        const input = ['<script>x</script>', 'safe', '<img src=x>'];
        const result = sanitizeForResponse(input);
        expect(result).to.be.an('array').with.length(3);
        expect(result[0]).to.not.include('script');
        expect(result[1]).to.equal('safe');
        expect(result[2]).to.not.include('img');
      });

      it('should handle mixed-type arrays', () => {
        const input = ['<b>bold</b>', 42, true, null];
        const result = sanitizeForResponse(input);
        expect(result).to.have.length(4);
        expect(result[0]).to.not.include('<');
        expect(result[1]).to.equal(42);
        expect(result[2]).to.be.true;
        expect(result[3]).to.be.null;
      });

      it('should handle empty arrays', () => {
        expect(sanitizeForResponse([])).to.deep.equal([]);
      });

      it('should handle nested arrays', () => {
        const input = [['<script>x</script>']];
        const result = sanitizeForResponse(input);
        expect(result[0][0]).to.not.include('script');
      });
    });

    describe('when given objects', () => {
      it('should sanitize string values in an object', () => {
        const input = { name: '<script>alert(1)</script>', age: 30 };
        const result = sanitizeForResponse(input);
        expect(result.name).to.not.include('script');
        expect(result.age).to.equal(30);
      });

      it('should handle nested objects', () => {
        const input = {
          user: {
            name: '<b>John</b>',
            bio: '<script>xss</script>',
          },
        };
        const result = sanitizeForResponse(input);
        expect(result.user.name).to.not.include('<');
        expect(result.user.bio).to.not.include('script');
      });

      it('should handle empty objects', () => {
        expect(sanitizeForResponse({})).to.deep.equal({});
      });

      it('should only process own properties', () => {
        const parent = { inherited: '<script>x</script>' };
        const child = Object.create(parent);
        child.own = 'safe';
        const result = sanitizeForResponse(child);
        expect(result).to.have.property('own', 'safe');
        expect(result).to.not.have.property('inherited');
      });

      it('should handle objects with null values', () => {
        const input = { a: null, b: 'safe' };
        const result = sanitizeForResponse(input);
        expect(result.a).to.be.null;
        expect(result.b).to.equal('safe');
      });

      it('should handle objects with array values', () => {
        const input = { items: ['<script>x</script>', 'safe'] };
        const result = sanitizeForResponse(input);
        expect(result.items[0]).to.not.include('script');
        expect(result.items[1]).to.equal('safe');
      });
    });
  });

  // -------------------------------------------------------------------------
  // validateBooleanParam
  // -------------------------------------------------------------------------
  describe('validateBooleanParam', () => {
    describe('when given null or undefined', () => {
      it('should return undefined for undefined', () => {
        expect(validateBooleanParam(undefined)).to.be.undefined;
      });

      it('should return undefined for null', () => {
        expect(validateBooleanParam(null)).to.be.undefined;
      });
    });

    describe('when given truthy values', () => {
      it('should return true for "true"', () => {
        expect(validateBooleanParam('true')).to.be.true;
      });

      it('should return true for "TRUE"', () => {
        expect(validateBooleanParam('TRUE')).to.be.true;
      });

      it('should return true for "True"', () => {
        expect(validateBooleanParam('True')).to.be.true;
      });

      it('should return true for "1"', () => {
        expect(validateBooleanParam('1')).to.be.true;
      });

      it('should return true for " true " (with spaces)', () => {
        expect(validateBooleanParam(' true ')).to.be.true;
      });
    });

    describe('when given falsy values', () => {
      it('should return false for "false"', () => {
        expect(validateBooleanParam('false')).to.be.false;
      });

      it('should return false for "FALSE"', () => {
        expect(validateBooleanParam('FALSE')).to.be.false;
      });

      it('should return false for "False"', () => {
        expect(validateBooleanParam('False')).to.be.false;
      });

      it('should return false for "0"', () => {
        expect(validateBooleanParam('0')).to.be.false;
      });

      it('should return false for empty string ""', () => {
        expect(validateBooleanParam('')).to.be.false;
      });

      it('should return false for " false " (with spaces)', () => {
        expect(validateBooleanParam(' false ')).to.be.false;
      });
    });

    describe('when given invalid values', () => {
      it('should throw BadRequestError for "yes"', () => {
        expect(() => validateBooleanParam('yes'))
          .to.throw(BadRequestError)
          .with.property('message')
          .that.includes('valid boolean');
      });

      it('should throw BadRequestError for "no"', () => {
        expect(() => validateBooleanParam('no'))
          .to.throw(BadRequestError)
          .with.property('message')
          .that.includes('valid boolean');
      });

      it('should throw BadRequestError for "abc"', () => {
        expect(() => validateBooleanParam('abc'))
          .to.throw(BadRequestError);
      });

      it('should throw BadRequestError for "2"', () => {
        expect(() => validateBooleanParam('2'))
          .to.throw(BadRequestError);
      });

      it('should include fieldName in error message', () => {
        expect(() => validateBooleanParam('invalid', 'isActive'))
          .to.throw(BadRequestError)
          .with.property('message')
          .that.includes('isActive');
      });

      it('should use default fieldName "parameter" when not specified', () => {
        expect(() => validateBooleanParam('invalid'))
          .to.throw(BadRequestError)
          .with.property('message')
          .that.includes('parameter');
      });
    });

    describe('when given XSS patterns', () => {
      it('should throw BadRequestError for script tag in boolean param', () => {
        expect(() => validateBooleanParam('<script>alert(1)</script>'))
          .to.throw(BadRequestError);
      });

      it('should throw BadRequestError for javascript: protocol in boolean param', () => {
        expect(() => validateBooleanParam('javascript:alert(1)'))
          .to.throw(BadRequestError);
      });
    });
  });
});
