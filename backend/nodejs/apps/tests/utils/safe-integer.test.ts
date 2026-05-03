import { expect } from 'chai';
import {
  safeParseInt,
  safeCalculateSkip,
  safeParsePagination,
  safeCalculateTotalPages,
} from '../../src/utils/safe-integer';

describe('safe-integer', () => {
  // -------------------------------------------------------------------------
  // safeParseInt
  // -------------------------------------------------------------------------
  describe('safeParseInt', () => {
    describe('when given null, undefined, or empty string', () => {
      it('should return defaultValue for null', () => {
        expect(safeParseInt(null, 10)).to.equal(10);
      });

      it('should return defaultValue for undefined', () => {
        expect(safeParseInt(undefined, 5)).to.equal(5);
      });

      it('should return defaultValue for empty string', () => {
        expect(safeParseInt('', 42)).to.equal(42);
      });
    });

    describe('when given valid numeric strings', () => {
      it('should parse "1"', () => {
        expect(safeParseInt('1', 0)).to.equal(1);
      });

      it('should parse "100"', () => {
        expect(safeParseInt('100', 0)).to.equal(100);
      });

      it('should parse large valid integer', () => {
        expect(safeParseInt('999999', 0)).to.equal(999999);
      });

      it('should parse string with leading zeros', () => {
        expect(safeParseInt('007', 0)).to.equal(7);
      });

      it('should parse string with trailing non-numeric characters (parseInt behavior)', () => {
        // parseInt('42abc') returns 42
        expect(safeParseInt('42abc', 0)).to.equal(42);
      });
    });

    describe('when given NaN-producing values', () => {
      it('should throw for non-numeric string "abc"', () => {
        expect(() => safeParseInt('abc', 0)).to.throw('Invalid number: abc');
      });

      it('should throw for "NaN"', () => {
        expect(() => safeParseInt('NaN', 0)).to.throw('Invalid number');
      });

      it('should throw for special characters', () => {
        expect(() => safeParseInt('!@#', 0)).to.throw('Invalid number');
      });
    });

    describe('when given values outside bounds', () => {
      it('should throw when value is below min', () => {
        expect(() => safeParseInt('0', 1, 1, 100)).to.throw('Number must be at least 1');
      });

      it('should throw when value is above max', () => {
        expect(() => safeParseInt('101', 1, 1, 100)).to.throw('Number must be at most 100');
      });

      it('should accept value equal to min', () => {
        expect(safeParseInt('5', 0, 5, 100)).to.equal(5);
      });

      it('should accept value equal to max', () => {
        expect(safeParseInt('100', 0, 1, 100)).to.equal(100);
      });
    });

    describe('when given values near MAX_SAFE_INTEGER', () => {
      it('should accept Number.MAX_SAFE_INTEGER', () => {
        const maxStr = Number.MAX_SAFE_INTEGER.toString();
        expect(safeParseInt(maxStr, 0, 1, Number.MAX_SAFE_INTEGER)).to.equal(Number.MAX_SAFE_INTEGER);
      });

      it('should throw for values exceeding MAX_SAFE_INTEGER', () => {
        // A number larger than MAX_SAFE_INTEGER
        const overflowStr = '99999999999999999999';
        expect(() => safeParseInt(overflowStr, 0)).to.throw('exceeds safe integer range');
      });
    });

    describe('when given negative numbers', () => {
      it('should throw when negative and min is 1 (default)', () => {
        expect(() => safeParseInt('-5', 0)).to.throw('Number must be at least 1');
      });

      it('should accept negative numbers when min allows it', () => {
        expect(safeParseInt('-5', 0, -10, 10)).to.equal(-5);
      });

      it('should throw for very large negative numbers', () => {
        const negOverflow = '-99999999999999999999';
        expect(() => safeParseInt(negOverflow, 0, -Number.MAX_SAFE_INTEGER)).to.throw('exceeds safe integer range');
      });
    });

    describe('default min and max parameters', () => {
      it('should use default min of 1', () => {
        expect(() => safeParseInt('0', 1)).to.throw('Number must be at least 1');
      });

      it('should use default max of MAX_SAFE_INTEGER', () => {
        expect(safeParseInt('1000000', 0)).to.equal(1000000);
      });
    });
  });

  // -------------------------------------------------------------------------
  // safeCalculateSkip
  // -------------------------------------------------------------------------
  describe('safeCalculateSkip', () => {
    describe('when given normal pagination values', () => {
      it('should return 0 for page 1, limit 10', () => {
        expect(safeCalculateSkip(1, 10)).to.equal(0);
      });

      it('should return 10 for page 2, limit 10', () => {
        expect(safeCalculateSkip(2, 10)).to.equal(10);
      });

      it('should return 20 for page 3, limit 10', () => {
        expect(safeCalculateSkip(3, 10)).to.equal(10 * 2);
      });

      it('should return 0 for page 1, limit 100', () => {
        expect(safeCalculateSkip(1, 100)).to.equal(0);
      });

      it('should return 100 for page 2, limit 100', () => {
        expect(safeCalculateSkip(2, 100)).to.equal(100);
      });

      it('should return 950 for page 20, limit 50', () => {
        expect(safeCalculateSkip(20, 50)).to.equal(950);
      });
    });

    describe('when limit is 0', () => {
      it('should return 0 for any page with limit 0', () => {
        expect(safeCalculateSkip(1, 0)).to.equal(0);
      });

      it('should return 0 for large page with limit 0', () => {
        expect(safeCalculateSkip(1000, 0)).to.equal(0);
      });
    });

    describe('when values would overflow', () => {
      it('should throw when page exceeds MAX_SAFE_INTEGER', () => {
        expect(() => safeCalculateSkip(Number.MAX_SAFE_INTEGER + 1, 10))
          .to.throw('Page or limit exceeds safe integer range');
      });

      it('should throw when limit exceeds MAX_SAFE_INTEGER', () => {
        expect(() => safeCalculateSkip(1, Number.MAX_SAFE_INTEGER + 1))
          .to.throw('Page or limit exceeds safe integer range');
      });

      it('should throw when multiplication would overflow', () => {
        // Very large page * limit that would exceed MAX_SAFE_INTEGER
        const largePage = Math.floor(Number.MAX_SAFE_INTEGER / 2);
        expect(() => safeCalculateSkip(largePage, 3))
          .to.throw('Pagination calculation would overflow');
      });
    });

    describe('when result would be negative', () => {
      it('should throw when skip is negative (page 0)', () => {
        // page=0 => (0-1)*limit = -limit
        expect(() => safeCalculateSkip(0, 10))
          .to.throw('Pagination skip value exceeds safe integer range');
      });
    });
  });

  // -------------------------------------------------------------------------
  // safeParsePagination
  // -------------------------------------------------------------------------
  describe('safeParsePagination', () => {
    describe('when given valid string values', () => {
      it('should parse page and limit correctly', () => {
        const result = safeParsePagination('2', '10');
        expect(result.page).to.equal(2);
        expect(result.limit).to.equal(10);
        expect(result.skip).to.equal(10);
      });

      it('should parse page 1 and limit 20', () => {
        const result = safeParsePagination('1', '20');
        expect(result.page).to.equal(1);
        expect(result.limit).to.equal(20);
        expect(result.skip).to.equal(0);
      });

      it('should parse large page number', () => {
        const result = safeParsePagination('100', '50');
        expect(result.page).to.equal(100);
        expect(result.limit).to.equal(50);
        expect(result.skip).to.equal(4950);
      });
    });

    describe('when given null or undefined', () => {
      it('should use default page and limit when both are null', () => {
        const result = safeParsePagination(null, null);
        expect(result.page).to.equal(1);
        expect(result.limit).to.equal(20);
        expect(result.skip).to.equal(0);
      });

      it('should use default page and limit when both are undefined', () => {
        const result = safeParsePagination(undefined, undefined);
        expect(result.page).to.equal(1);
        expect(result.limit).to.equal(20);
        expect(result.skip).to.equal(0);
      });

      it('should use default page when page is null', () => {
        const result = safeParsePagination(null, '50');
        expect(result.page).to.equal(1);
        expect(result.limit).to.equal(50);
        expect(result.skip).to.equal(0);
      });

      it('should use default limit when limit is null', () => {
        const result = safeParsePagination('3', null);
        expect(result.page).to.equal(3);
        expect(result.limit).to.equal(20);
        expect(result.skip).to.equal(40);
      });
    });

    describe('when using custom defaults', () => {
      it('should use custom defaultPage', () => {
        const result = safeParsePagination(null, null, 5, 10);
        expect(result.page).to.equal(5);
        expect(result.limit).to.equal(10);
      });

      it('should use custom defaultLimit', () => {
        const result = safeParsePagination(null, null, 1, 50);
        expect(result.limit).to.equal(50);
      });

      it('should use custom maxLimit', () => {
        const result = safeParsePagination('1', '200', 1, 20, 200);
        expect(result.limit).to.equal(200);
      });
    });

    describe('when given invalid values', () => {
      it('should throw for non-numeric page', () => {
        expect(() => safeParsePagination('abc', '10')).to.throw('Invalid number');
      });

      it('should throw for non-numeric limit', () => {
        expect(() => safeParsePagination('1', 'xyz')).to.throw('Invalid number');
      });

      it('should throw for page 0 (below min of 1)', () => {
        expect(() => safeParsePagination('0', '10')).to.throw('Number must be at least 1');
      });

      it('should throw for limit exceeding maxLimit (default 100)', () => {
        expect(() => safeParsePagination('1', '101')).to.throw('Number must be at most 100');
      });

      it('should throw for negative page', () => {
        expect(() => safeParsePagination('-1', '10')).to.throw();
      });

      it('should throw for negative limit', () => {
        expect(() => safeParsePagination('1', '-5')).to.throw();
      });
    });
  });

  // -------------------------------------------------------------------------
  // safeCalculateTotalPages
  // -------------------------------------------------------------------------
  describe('safeCalculateTotalPages', () => {
    describe('when given normal values', () => {
      it('should return 1 for totalCount=1, limit=10', () => {
        expect(safeCalculateTotalPages(1, 10)).to.equal(1);
      });

      it('should return 1 for totalCount=10, limit=10', () => {
        expect(safeCalculateTotalPages(10, 10)).to.equal(1);
      });

      it('should return 2 for totalCount=11, limit=10', () => {
        expect(safeCalculateTotalPages(11, 10)).to.equal(2);
      });

      it('should return 10 for totalCount=100, limit=10', () => {
        expect(safeCalculateTotalPages(100, 10)).to.equal(10);
      });

      it('should return 0 for totalCount=0, limit=10', () => {
        expect(safeCalculateTotalPages(0, 10)).to.equal(0);
      });

      it('should return 1 for totalCount=1, limit=1', () => {
        expect(safeCalculateTotalPages(1, 1)).to.equal(1);
      });

      it('should return correct value for totalCount=99, limit=10', () => {
        expect(safeCalculateTotalPages(99, 10)).to.equal(10);
      });

      it('should return correct value for large numbers', () => {
        expect(safeCalculateTotalPages(1000000, 100)).to.equal(10000);
      });
    });

    describe('when given invalid inputs', () => {
      it('should throw for negative totalCount', () => {
        expect(() => safeCalculateTotalPages(-1, 10))
          .to.throw('Invalid totalCount or limit for page calculation');
      });

      it('should throw for limit=0', () => {
        expect(() => safeCalculateTotalPages(10, 0))
          .to.throw('Invalid totalCount or limit for page calculation');
      });

      it('should throw for negative limit', () => {
        expect(() => safeCalculateTotalPages(10, -5))
          .to.throw('Invalid totalCount or limit for page calculation');
      });
    });

    describe('when values would overflow', () => {
      it('should throw when totalCount exceeds MAX_SAFE_INTEGER', () => {
        expect(() => safeCalculateTotalPages(Number.MAX_SAFE_INTEGER + 1, 10))
          .to.throw('Total count or limit exceeds safe integer range');
      });

      it('should throw when limit exceeds MAX_SAFE_INTEGER', () => {
        expect(() => safeCalculateTotalPages(10, Number.MAX_SAFE_INTEGER + 1))
          .to.throw('Total count or limit exceeds safe integer range');
      });

      it('should throw when addition totalCount + limit - 1 would overflow', () => {
        // totalCount close to MAX_SAFE_INTEGER with a non-trivial limit
        const nearMax = Number.MAX_SAFE_INTEGER;
        expect(() => safeCalculateTotalPages(nearMax, 10))
          .to.throw('Total pages calculation would overflow');
      });
    });

    describe('when totalPages result exceeds MAX_SAFE_INTEGER', () => {
      it('should throw when Math.ceil result exceeds MAX_SAFE_INTEGER', () => {
        // Stub Math.ceil to return a value > MAX_SAFE_INTEGER to test the defensive check
        const originalCeil = Math.ceil;
        Math.ceil = () => Number.MAX_SAFE_INTEGER + 1;
        try {
          expect(() => safeCalculateTotalPages(10, 3)).to.throw('Total pages exceeds safe integer range');
        } finally {
          Math.ceil = originalCeil;
        }
      });
    });

    describe('boundary values', () => {
      it('should handle totalCount=1, limit=MAX_SAFE_INTEGER', () => {
        expect(safeCalculateTotalPages(1, Number.MAX_SAFE_INTEGER)).to.equal(1);
      });

      it('should handle totalCount equals limit', () => {
        expect(safeCalculateTotalPages(50, 50)).to.equal(1);
      });

      it('should handle totalCount one more than limit', () => {
        expect(safeCalculateTotalPages(51, 50)).to.equal(2);
      });

      it('should handle totalCount one less than limit', () => {
        expect(safeCalculateTotalPages(49, 50)).to.equal(1);
      });
    });
  });
});
