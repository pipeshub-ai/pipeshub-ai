/**
 * SIGNGAP SG-UI-1..SG-UI-6: a server whose workers cannot carry a person's own
 * Claude sign-in (the sign-in add-on is off). The person's "Use my Claude
 * account" is greyed with a plain reason and a link to
 * the install guide (never a dead link); saving is refused by the server; a
 * task that ran on the org's account instead says so once in the chat. With
 * the add-on nothing changes.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { RELEASE_DEFAULTS } from '@/lib/contracts/harness/generated/core/policy-data';
import { useToastStore } from '@/lib/store/toast-store';
import { EngineSignInSection } from '../components/engine-signin-section';
import { SIGNIN_ADDON_GUIDE, toEngineSignInView } from '../engine-signin-api';
import { RunNotices } from '@/app/(main)/chat/components/message-area/agent-activity';
import { freshState, installFake, type FakeState } from './fake-harness';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

let state: FakeState;

const REASON = 'Personal Claude accounts need the sign-in add-on on this server.';
const GUIDE = 'See the install guide';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

beforeEach(() => {
  state = freshState();
  state.signin.carried = false;
  installFake(apiClient, state);
  useToastStore.setState({ toasts: [] });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const guideLink = (scope: HTMLElement): HTMLAnchorElement =>
  within(scope).getByRole('link', { name: GUIDE }) as HTMLAnchorElement;

describe('SIGNGAP: the install guide link', () => {
  it('SG-UI-1: points at the section of the install guide this release ships, never anywhere else', () => {
    expect(SIGNIN_ADDON_GUIDE).toBe(`${RELEASE_DEFAULTS.engineIsolation.installGuide}#personal-claude-accounts-the-sign-in-add-on`);
  });
});

describe('SIGNGAP: the person\'s "Use my Claude account"', () => {
  it('SG-UI-2: greyed with the reason and the guide, even for someone who turned it on before', async () => {
    state.signin.org = true;
    state.signin.enabled = true;
    render(<Theme><EngineSignInSection /></Theme>);
    const row = await screen.findByRole('group', { name: 'Claude account' });
    expect(row.getAttribute('data-signin-state')).toBe('not_allowed');
    expect(within(row).getByText('Not available on this server')).toBeTruthy();
    expect(within(row).getByTestId('signin-addon-reason').textContent).toBe(
      `${REASON} Until it is installed, your Claude Code tasks use your organization's account. ${GUIDE}`,
    );
    const use = within(row).getByRole('button', { name: 'Use my Claude account for my tasks' });
    expect(use.hasAttribute('disabled')).toBe(true);
    const link = guideLink(row);
    expect(link.getAttribute('href')).toBe(SIGNIN_ADDON_GUIDE);
    expect(link.getAttribute('rel')).toContain('noopener');
    expect(within(row).queryByText('Not allowed by your organization')).toBeNull();
  });

  it('SG-UI-3: the view keeps the server\'s reason', () => {
    expect(
      toEngineSignInView({ claude: { state: 'not_allowed', reason: 'engine_signin_unavailable' } }).claude.reason,
    ).toBe('engine_signin_unavailable');
  });
});

describe('SIGNGAP: the chat', () => {
  it('SG-UI-6: a task that ran on the org\'s account says why, once', () => {
    render(
      <Theme>
        <RunNotices
          parts={[
            { type: 'notice', code: 'signin_unavailable', runId: 'run-1' },
            { type: 'notice', code: 'signin_unavailable', runId: 'run-1' },
          ]}
        />
      </Theme>,
    );
    expect(screen.getAllByRole('note').map((note) => note.textContent)).toEqual([
      "Your Claude account can't be used on this server yet, so this task ran on your organization's account.",
    ]);
  });
});
