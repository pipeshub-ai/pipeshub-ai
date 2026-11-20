/**
 * Browsers and password managers treat every `type="password"` field as an account
 * login: they fill the saved email into the text field before it (a model name, an
 * endpoint) and the saved password into the field itself, and offer to save whatever
 * the user types as a site password. So only real account-credential forms may render
 * a password input. Secrets such as API keys, tokens and client secrets use
 * `SecretTextField` / `secretInputProps` from `app/components/ui/secret-text-field`.
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const APP_DIR = path.resolve(__dirname, '../../..');

/** Account-credential forms: the user's own sign-in password or one-time code. */
const CREDENTIAL_FORMS = new Set([
  'app/(public)/forms/form-components/password-field.tsx',
  'app/(public)/forms/form-components/otp-field.tsx',
  'app/(main)/workspace/profile/components/change-password-dialog.tsx',
]);

// JSX only: type="password", type={'password'}, type={x ? 'text' : 'password'}.
// Not schema data (`type: 'password'`) or comparisons (`field.type === 'password'`).
const PASSWORD_TYPE = /(^|[^.\w])type=\{?[^\n}]*['"]password['"]/;

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name === '__tests__') continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) out.push(full);
  }
  return out;
}

describe('password inputs', () => {
  it('appear only in account-credential forms', () => {
    const root = path.resolve(APP_DIR, '..');
    const offenders: string[] = [];
    for (const file of walk(APP_DIR)) {
      const rel = path.relative(root, file).split(path.sep).join('/');
      if (CREDENTIAL_FORMS.has(rel)) continue;
      fs.readFileSync(file, 'utf8')
        .split('\n')
        .forEach((line, i) => {
          if (PASSWORD_TYPE.test(line)) offenders.push(`${rel}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(offenders).toEqual([]);
  });

  it('allow-list only files that still render a password input', () => {
    const root = path.resolve(APP_DIR, '..');
    for (const rel of CREDENTIAL_FORMS) {
      expect(PASSWORD_TYPE.test(fs.readFileSync(path.join(root, rel), 'utf8')), rel).toBe(true);
    }
  });
});
