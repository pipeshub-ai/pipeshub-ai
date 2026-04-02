import { expect } from 'chai';
import { deriveNameFromEmail } from '../../src/utils/generic-functions';

describe('generic-functions', () => {
  // -------------------------------------------------------------------------
  // deriveNameFromEmail
  // -------------------------------------------------------------------------
  describe('deriveNameFromEmail', () => {
    describe('when given undefined or missing email', () => {
      it('should return default values for undefined', () => {
        const result = deriveNameFromEmail(undefined);
        expect(result.firstName).to.equal('User');
        expect(result.lastName).to.equal('');
        expect(result.fullName).to.equal('User');
      });

      it('should return default values for empty string', () => {
        const result = deriveNameFromEmail('');
        expect(result.firstName).to.equal('User');
        expect(result.lastName).to.equal('');
        expect(result.fullName).to.equal('User');
      });
    });

    describe('when given email without @ sign', () => {
      it('should return default values for string without @', () => {
        const result = deriveNameFromEmail('noemail');
        expect(result.firstName).to.equal('User');
        expect(result.lastName).to.equal('');
        expect(result.fullName).to.equal('User');
      });
    });

    describe('when given standard email formats', () => {
      it('should parse first.last@domain.com', () => {
        const result = deriveNameFromEmail('john.doe@example.com');
        expect(result.firstName).to.equal('John');
        expect(result.lastName).to.equal('Doe');
        expect(result.fullName).to.equal('John Doe');
      });

      it('should parse first_last@domain.com', () => {
        const result = deriveNameFromEmail('jane_smith@example.com');
        expect(result.firstName).to.equal('Jane');
        expect(result.lastName).to.equal('Smith');
        expect(result.fullName).to.equal('Jane Smith');
      });

      it('should parse first-last@domain.com', () => {
        const result = deriveNameFromEmail('bob-jones@example.com');
        expect(result.firstName).to.equal('Bob');
        expect(result.lastName).to.equal('Jones');
        expect(result.fullName).to.equal('Bob Jones');
      });
    });

    describe('when given single-name emails', () => {
      it('should parse single name before @', () => {
        const result = deriveNameFromEmail('alice@example.com');
        expect(result.firstName).to.equal('Alice');
        expect(result.lastName).to.equal('');
        expect(result.fullName).to.equal('Alice');
      });

      it('should capitalize first letter of a lowercase name', () => {
        const result = deriveNameFromEmail('john@example.com');
        expect(result.firstName).to.equal('John');
      });

      it('should handle all-uppercase single name', () => {
        const result = deriveNameFromEmail('JOHN@example.com');
        expect(result.firstName).to.equal('John');
      });
    });

    describe('when given emails with trailing numbers', () => {
      it('should strip trailing numbers from the local part', () => {
        const result = deriveNameFromEmail('john.doe123@example.com');
        expect(result.firstName).to.equal('John');
        expect(result.lastName).to.equal('Doe');
        expect(result.fullName).to.equal('John Doe');
      });

      it('should strip trailing numbers from single name', () => {
        const result = deriveNameFromEmail('alice42@example.com');
        expect(result.firstName).to.equal('Alice');
        expect(result.lastName).to.equal('');
        expect(result.fullName).to.equal('Alice');
      });

      it('should handle email that is all numbers before @', () => {
        // '12345' -> cleaned = '' -> parts = [] -> firstName = 'User'
        const result = deriveNameFromEmail('12345@example.com');
        expect(result.firstName).to.equal('User');
        expect(result.lastName).to.equal('');
        expect(result.fullName).to.equal('User');
      });
    });

    describe('when given emails with multiple delimiters', () => {
      it('should use only the first two parts for first and last name', () => {
        const result = deriveNameFromEmail('john.michael.doe@example.com');
        expect(result.firstName).to.equal('John');
        expect(result.lastName).to.equal('Michael');
        expect(result.fullName).to.equal('John Michael');
      });

      it('should handle mixed delimiters', () => {
        const result = deriveNameFromEmail('john_doe-smith@example.com');
        expect(result.firstName).to.equal('John');
        expect(result.lastName).to.equal('Doe');
        expect(result.fullName).to.equal('John Doe');
      });
    });

    describe('when given emails with numbers in the middle', () => {
      it('should keep numbers that are not trailing', () => {
        // '123john' -> cleaned = '123john' (no trailing digits to strip)
        // Actually, \d+$ strips trailing digits. '123john' has no trailing digits.
        // parts = ['123john'] -> firstName = '123john' capitalized
        const result = deriveNameFromEmail('j2ohn@example.com');
        expect(result.firstName).to.equal('J2ohn');
      });
    });

    describe('when the local part becomes empty after cleaning', () => {
      it('should return default when local part is empty after number stripping', () => {
        // e.g., the local part is all digits: '999@example.com'
        const result = deriveNameFromEmail('999@example.com');
        expect(result.firstName).to.equal('User');
        expect(result.lastName).to.equal('');
        expect(result.fullName).to.equal('User');
      });
    });

    describe('when given edge case formats', () => {
      it('should handle email with @ at the beginning', () => {
        // '@example.com' -> localPart = '' -> falsy -> default
        const result = deriveNameFromEmail('@example.com');
        expect(result.firstName).to.equal('User');
        expect(result.lastName).to.equal('');
        expect(result.fullName).to.equal('User');
      });

      it('should handle email with multiple @ signs (uses first part)', () => {
        // 'a@b@c.com' -> split('@').shift() = 'a'
        const result = deriveNameFromEmail('a@b@c.com');
        expect(result.firstName).to.equal('A');
        expect(result.lastName).to.equal('');
        expect(result.fullName).to.equal('A');
      });

      it('should handle single character local part', () => {
        const result = deriveNameFromEmail('a@example.com');
        expect(result.firstName).to.equal('A');
        expect(result.lastName).to.equal('');
        expect(result.fullName).to.equal('A');
      });

      it('should handle delimiter-only local part', () => {
        // '._-@example.com' -> cleaned = '._-' -> split by delimiters -> parts = [] (all empty strings filtered)
        const result = deriveNameFromEmail('._-@example.com');
        expect(result.firstName).to.equal('User');
        expect(result.lastName).to.equal('');
        expect(result.fullName).to.equal('User');
      });
    });

    describe('capitalization behavior', () => {
      it('should capitalize first letter and lowercase the rest for first name', () => {
        const result = deriveNameFromEmail('jOHN.dOE@example.com');
        expect(result.firstName).to.equal('John');
        expect(result.lastName).to.equal('Doe');
      });

      it('should handle already-capitalized names', () => {
        const result = deriveNameFromEmail('John.Doe@example.com');
        expect(result.firstName).to.equal('John');
        expect(result.lastName).to.equal('Doe');
      });

      it('should handle all-lowercase names', () => {
        const result = deriveNameFromEmail('john.doe@example.com');
        expect(result.firstName).to.equal('John');
        expect(result.lastName).to.equal('Doe');
      });
    });
  });
});
