/**
 * SEC-CR-07: before a message holding a secret is sent, the composer says the
 * secret will be removed and offers the credential store; each choice is one
 * button with plain words.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const { PastedSecretDialog, PERSONAL_CREDENTIALS_PATH } = await import('../components/pasted-secret-dialog');

afterEach(() => cleanup());

function renderDialog() {
  const handlers = { onSendWithout: vi.fn(), onSaveAsCredential: vi.fn(), onKeepEditing: vi.fn() };
  render(
    <Theme>
      <PastedSecretDialog open {...handlers} />
    </Theme>,
  );
  return handlers;
}

describe('PastedSecretDialog (SEC-CR-07)', () => {
  it('says the secret is removed and where to keep it instead', () => {
    renderDialog();
    const dialog = screen.getByRole('dialog');
    expect(dialog.textContent).toContain('This message has a secret in it');
    expect(dialog.textContent).toContain('PipesHub removes them before sending');
    expect(dialog.textContent).toContain('save it as a credential on your Actions page');
  });

  it('each button makes its one choice', () => {
    const handlers = renderDialog();
    fireEvent.click(screen.getByRole('button', { name: 'Send without it' }));
    expect(handlers.onSendWithout).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Save as a credential' }));
    expect(handlers.onSaveAsCredential).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(handlers.onKeepEditing).toHaveBeenCalledTimes(1);
  });

  it('the credential link is a page that exists', async () => {
    const { existsSync } = await import('node:fs');
    const { join } = await import('node:path');
    const page = join(__dirname, '..', '..', ...PERSONAL_CREDENTIALS_PATH.split('/').filter(Boolean), 'page.tsx');
    expect(existsSync(page)).toBe(true);
  });
});
