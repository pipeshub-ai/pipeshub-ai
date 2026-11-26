import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import en from '@/lib/i18n/locales/en-US.json';

const getSlackIdentityLinks = vi.fn();
const revokeSlackIdentityLink = vi.fn();
vi.mock('../api', () => ({
  BotsApi: {
    getSlackIdentityLinks: (...a: unknown[]) => getSlackIdentityLinks(...a),
    revokeSlackIdentityLink: (...a: unknown[]) => revokeSlackIdentityLink(...a),
  },
}));
vi.mock('@/lib/store/toast-store', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { SlackIdentityLinks } from '../components/slack-identity-links';

const words = en.workspace.bots.identityLinks;
const link = {
  teamId: 'T0HOME',
  slackUserId: 'U0ALICE',
  slackName: 'Alice',
  userId: 'u1',
  email: 'alice@example.com',
  fullName: 'Alice A',
  linkedAt: '2026-11-13T00:00:00Z',
  active: true,
};

const renderIt = () => render(<Theme><SlackIdentityLinks /></Theme>);

beforeEach(() => {
  getSlackIdentityLinks.mockReset();
  revokeSlackIdentityLink.mockReset();
});
afterEach(() => cleanup());

describe('SlackIdentityLinks (R38-IDENT SEC-C-03 admin view)', () => {
  it('names each Slack person and the account they act as', async () => {
    getSlackIdentityLinks.mockResolvedValue([link, { ...link, slackUserId: 'U0BOB', slackName: 'Bob', active: false, email: null, fullName: null }]);
    renderIt();
    expect(await screen.findByText('Alice A')).toBeTruthy();
    expect(screen.getByText('alice@example.com')).toBeTruthy();
    expect(screen.getByText(words.inactive)).toBeTruthy();
  });

  it('unlinks a person and reloads', async () => {
    getSlackIdentityLinks.mockResolvedValueOnce([link]).mockResolvedValueOnce([]);
    revokeSlackIdentityLink.mockResolvedValue(undefined);
    renderIt();
    fireEvent.click(await screen.findByText(words.remove));
    await waitFor(() => expect(screen.getByText(words.empty)).toBeTruthy());
    expect(revokeSlackIdentityLink).toHaveBeenCalledWith('T0HOME', 'U0ALICE');
  });

  it('says so when the list cannot load', async () => {
    getSlackIdentityLinks.mockRejectedValue(new Error('boom'));
    renderIt();
    expect(await screen.findByText(words.loadFailed)).toBeTruthy();
  });
});
