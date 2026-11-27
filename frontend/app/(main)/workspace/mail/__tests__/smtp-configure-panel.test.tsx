import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { CONFIG_SECRET_PLACEHOLDER } from '@/lib/constants/config-secret-placeholder';

vi.mock('@/app/(main)/workspace/components/workspace-right-panel', () => ({
  WorkspaceRightPanel: ({ children, onPrimaryClick }: { children: React.ReactNode; onPrimaryClick: () => void }) => (
    <div>
      {children}
      <button onClick={onPrimaryClick}>Save</button>
    </div>
  ),
}));
vi.mock('@/lib/hooks/use-secret-reveal-available', () => ({
  useSecretRevealAvailable: () => false,
  useRevealScope: () => () => () => true,
}));
vi.mock('../api', () => ({ SmtpApi: { revealSmtpConfig: vi.fn() } }));

import { SmtpConfigurePanel } from '../components/smtp-configure-panel';

const MASKED = {
  host: CONFIG_SECRET_PLACEHOLDER,
  port: 587,
  username: CONFIG_SECRET_PLACEHOLDER,
  password: CONFIG_SECRET_PLACEHOLDER,
  fromEmail: CONFIG_SECRET_PLACEHOLDER,
};

function renderPanel(fromEmail: string) {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(
    <Theme>
      <SmtpConfigurePanel
        open
        onClose={vi.fn()}
        onSaveSuccess={vi.fn()}
        initialConfig={{ ...MASKED, fromEmail }}
        onSave={onSave}
      />
    </Theme>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  return onSave;
}

afterEach(cleanup);

describe('SMTP settings whose stored values are hidden', () => {
  it('saves them back as they are', async () => {
    const onSave = renderPanel(CONFIG_SECRET_PLACEHOLDER);
    await waitFor(() => expect(onSave).toHaveBeenCalledWith(MASKED));
  });

  it('still refuses a from address that is not an email address', async () => {
    const onSave = renderPanel('not-an-address');
    await waitFor(() => expect(screen.getByText(/valid email/i)).toBeTruthy());
    expect(onSave).not.toHaveBeenCalled();
  });
});
