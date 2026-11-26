/**
 * SIGNIN CARD-1..CARD-9: the chat's sign-in card when Claude Code asks the
 * person to sign in. Only Anthropic's own link is shown; the code is a
 * password field and goes straight to the sandbox; the organization's
 * account is an explicit choice, never a fallback; a plan run shows its chip.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { freshState, installFake, type FakeState } from '@/app/(main)/workspace/ai-accounts/__tests__/fake-harness';
import { EngineSignInCard } from '../engine-signin-card';
import { latestSignIn, onOwnPlan, signInReasonKey } from '../../../engine-signin';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const LINK = 'https://claude.com/cai/oauth/authorize?code=true&state=s1';
const LINE = { attemptId: 'sin_run-1.a1', state: 'requested' as const, reasonCode: null, plan: null };
let state: FakeState;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

beforeEach(() => {
  state = freshState();
  state.signin.org = true;
  state.signin.enabled = true;
  state.signin.attempt = { state: 'waiting', link: LINK };
  installFake(apiClient, state);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const renderCard = (line = LINE, active = true) => render(<Theme><EngineSignInCard line={line} active={active} /></Theme>);

describe('EngineSignInCard', () => {
  it("CARD-1: Anthropic's link opens in a new tab without the opener, and the code field is masked", async () => {
    renderCard();
    const link = await screen.findByRole('link', { name: /Open Anthropic's sign-in page/ });
    expect(link.getAttribute('href')).toBe(LINK);
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
    const field = screen.getByLabelText('Code from Anthropic');
    // Masked, but not a password field: a password manager would offer to save the code as this site's password.
    expect(field.getAttribute('type')).toBe('text');
    expect(field.closest('.secret-field-masked')).toBeTruthy();
    expect(field.getAttribute('autocomplete')).toBe('off');
    expect((screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("CARD-2: a link off Anthropic's hosts is never shown", async () => {
    state.signin.attempt = { state: 'waiting', link: 'https://claude.com.evil.example/oauth/authorize' };
    renderCard();
    expect(await screen.findByText(/not Anthropic's, so it is not shown/)).toBeTruthy();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('CARD-3: the pasted code goes to its attempt once; signed in, the card becomes the plan chip', async () => {
    renderCard();
    fireEvent.change(await screen.findByLabelText('Code from Anthropic'), { target: { value: ' CODE123#s1 ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByTestId('own-plan-chip')).toBeTruthy();
    expect(screen.getByTestId('own-plan-chip').textContent).toContain('Using your Claude plan · Claude Team');
    const posts = state.calls.filter((c) => c.method === 'POST');
    expect(posts).toEqual([{ method: 'POST', url: '/api/v1/agent-harness/engine-accounts/signin/sin_run-1.a1/code', body: { code: 'CODE123#s1' } }]);
  });

  it('CARD-4: a code Anthropic refused says so, clears the field and shows the new link', async () => {
    state.signin.code = { state: 'failed', reasonCode: 'signin_failed', retry: true };
    renderCard();
    const field = await screen.findByLabelText('Code from Anthropic');
    fireEvent.change(field, { target: { value: 'BAD#s1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByText('Anthropic did not accept the code. Try again with a new code.')).toBeTruthy();
    expect((screen.getByLabelText('Code from Anthropic') as HTMLInputElement).value).toBe('');
    await waitFor(() => expect(state.calls.filter((c) => c.method === 'GET').length).toBe(2));
  });

  it("CARD-5: using the organization's account is explicit: it turns the person's choice off and ends this sign-in", async () => {
    renderCard();
    fireEvent.click(await screen.findByRole('button', { name: 'Use organization account instead' }));
    expect(await screen.findByText(/Send your message again to run it on your organization's account/)).toBeTruthy();
    expect(state.calls.filter((c) => c.method === 'PUT').map((c) => c.body)).toEqual([{ enabled: false }]);
    expect(state.calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/signin/sin_run-1.a1'))).toBe(true);
  });

  it('CARD-6: an ended sign-in (not found) says so and offers no code field', async () => {
    state.signin.attempt = null;
    renderCard();
    expect(await screen.findByText('This sign-in has ended.')).toBeTruthy();
    expect(screen.queryByLabelText('Code from Anthropic')).toBeNull();
  });

  it('CARD-7: a failed sign-in names why, and still offers the explicit choice', async () => {
    renderCard({ ...LINE, state: 'failed', reasonCode: 'plan_not_allowed' });
    expect(await screen.findByText(/allows only Team and Enterprise plans/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Use organization account instead' })).toBeTruthy();
    expect(state.calls).toEqual([]);
  });

  it('CARD-8: touch targets are at least 44 px', async () => {
    renderCard();
    await screen.findByRole('link', { name: /Open Anthropic's sign-in page/ });
    for (const name of ['Continue', 'Use organization account instead', 'Cancel']) {
      expect((screen.getByRole('button', { name }) as HTMLElement).style.minHeight).toBe('44px');
    }
  });
});

describe('EngineSignInCard (CARDFAIL-1)', () => {
  it('CARD-10: PipesHub restarting while the code is sent: the card says so and offers Try again; nothing is resent unasked', async () => {
    const CODE = 'POST /engine-accounts/signin/sin_run-1.a1/code';
    state.fail[CODE] = { statusCode: 503, message: 'restarting' } as never;
    renderCard();
    fireEvent.change(await screen.findByLabelText('Code from Anthropic'), { target: { value: 'CODE123#s1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    const notice = await screen.findByTestId('card-send-notice');
    expect(notice.textContent).toContain("Couldn't send your code: PipesHub is restarting.");
    expect(notice.getAttribute('data-phase')).toBe('stopped');
    const posts = () => state.calls.filter((c) => c.method === 'POST' && c.url.endsWith('/code'));
    expect(posts()).toHaveLength(1);
    // The code the person pasted stays; Try again sends that one.
    delete state.fail[CODE];
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByTestId('own-plan-chip')).toBeTruthy();
    expect(posts().map((c) => c.body)).toEqual([{ code: 'CODE123#s1' }, { code: 'CODE123#s1' }]);
  });
});

describe('sign-in lines', () => {
  it('CARD-9: the latest line wins; a plan run is one that signed in; every reason has its copy', () => {
    const frames = [
      { tool: 'code_task', taskId: 't', kind: 'command', text: 'ls' },
      { tool: 'code_task', taskId: 't', kind: 'signin', text: '', data: { attemptId: 'sin_1', state: 'requested' } },
      { tool: 'code_task', taskId: 't', kind: 'signin', text: '', data: { attemptId: 'sin_1', state: 'signed_in', plan: 'max' } },
    ];
    expect(latestSignIn(frames)).toEqual({ attemptId: 'sin_1', state: 'signed_in', reasonCode: null, plan: 'max' });
    expect(onOwnPlan(frames)).toBe(true);
    expect(onOwnPlan(frames.slice(0, 2))).toBe(false);
    expect(latestSignIn([{ tool: 'code_task', taskId: 't', kind: 'signin', text: '', data: { state: 'bogus' } }])).toBeNull();
    for (const code of ['signin_code_invalid', 'signin_failed', 'plan_not_allowed', 'signin_timeout', 'signin_cancelled',
      'signin_url_refused', 'signin_unavailable', 'account_disconnected', 'account_expired', 'plan_limit_reached',
      'signin_required', 'account_shared_session', 'account_requires_interactive', 'ENGINE_SIGNIN_OFF']) {
      const key = signInReasonKey(code);
      expect(key, code).not.toBeNull();
      expect(i18n.t(key as string), code).not.toBe(key);
    }
    expect(signInReasonKey('budget_exhausted')).toBeNull();
  });
});
