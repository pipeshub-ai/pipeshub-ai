import { expect } from 'chai';
import { readFileSync } from 'fs';
import path from 'path';

// F32: which 401 a refusal throws decides its error_class. A credential the
// request submitted (a code, a password, a CAPTCHA, an identity-provider
// token), wrong, expired or already used, is a WrongCredentialError
// (bad_request); the request's own session or token refused is an
// UnauthorizedError (auth_expired). Keyed on the messages, not on lines, so a
// new throw of the same message, or a reformat, is still checked.

const SOURCES = [
  'src/modules/auth/controller/userAccount.controller.ts',
  'src/modules/auth/utils/azureAdTokenValidation.ts',
].map((file) => readFileSync(path.resolve(__dirname, '../../..', file), 'utf8'));

const SUBMITTED_CREDENTIAL = [
  'WRONG_SIGN_IN_CODE',
  'OTP_ALREADY_USED',
  'SIGN_IN_ACCOUNT_CHANGED',
  'PROVIDER_SHARED_NO_EMAIL',
  'MICROSOFT_SIGN_IN_FAILED',
  'WRONG_ACCOUNT',
  "'Invalid CAPTCHA verification.",
  "'Current password is incorrect.'",
  `"Sign-in with Google didn't complete.`,
  `"This Microsoft account doesn't match`,
  "'Your Microsoft sign-in expired.",
];

const OWN_SESSION = [
  'SESSION_NO_LONGER_VALID',
  'RESET_LINK_ALREADY_USED',
  'ACCOUNT_NO_LONGER_ACTIVE',
  "'Session expired, please login again'",
  "'No token provided'",
  "'User not authenticated'",
];

/** Every `new XError(...)` in the sources, with its whole argument text. */
function constructions(): { type: string; args: string }[] {
  const found: { type: string; args: string }[] = [];
  for (const source of SOURCES) {
    for (const match of source.matchAll(/new (\w+Error)\(/g)) {
      let depth = 1;
      let end = match.index! + match[0].length;
      while (depth > 0 && end < source.length) {
        if (source[end] === '(') depth += 1;
        if (source[end] === ')') depth -= 1;
        end += 1;
      }
      found.push({
        type: match[1]!,
        args: source.slice(match.index! + match[0].length, end - 1),
      });
    }
  }
  return found;
}

const carrying = (message: string) => {
  const pattern = /^\w+$/.test(message)
    ? new RegExp(`\\b${message}\\b`)
    : { test: (args: string) => args.includes(message) };
  return constructions()
    .filter(({ args }) => pattern.test(args))
    .map(({ type }) => type);
};

describe('auth 401 sites: a submitted credential vs the request’s own session (F32)', () => {
  for (const message of SUBMITTED_CREDENTIAL) {
    it(`${message} is thrown only as a WrongCredentialError`, () => {
      const types = carrying(message);
      expect(types, 'thrown at least once').to.include('WrongCredentialError');
      expect(types).to.not.include('UnauthorizedError');
    });
  }

  for (const message of OWN_SESSION) {
    it(`${message} stays an UnauthorizedError`, () => {
      const types = carrying(message);
      expect(types, 'thrown at least once').to.include('UnauthorizedError');
      expect(types).to.not.include('WrongCredentialError');
    });
  }
});
