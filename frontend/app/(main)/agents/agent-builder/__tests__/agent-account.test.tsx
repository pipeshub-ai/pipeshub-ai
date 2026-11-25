/**
 * U2 AG-1..AG-3, AG-6: an engine agent's account choice. The choice is the
 * agent layer's `accountKinds` (E4a/A1); kinds no available vendor row offers
 * are disabled with their reason; an outage keeps the stored value.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { freshState, installFake, rule, type FakeState } from '@/app/(main)/workspace/ai-accounts/__tests__/fake-harness';
import type { EngineProfileNarrowing } from '@/app/(main)/workspace/ai-accounts/types';
import { accountKindChoice, isEngineProfile, withAccountKinds } from '../agent-account';
import { AgentAccountChoice } from '../components/agent-account-choice';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

let state: FakeState;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

beforeEach(() => {
  state = freshState();
  installFake(apiClient, state);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderChoice(engineProfile: EngineProfileNarrowing, onChange = vi.fn()) {
  render(
    <Theme>
      <AgentAccountChoice engineProfile={engineProfile} onChange={onChange} />
    </Theme>,
  );
  return onChange;
}

describe('agent account helpers', () => {
  it('AG-3: accountKindChoice and withAccountKinds keep every other dimension', () => {
    expect(accountKindChoice(undefined)).toBe('any');
    expect(accountKindChoice({})).toBe('any');
    expect(accountKindChoice({ accountKinds: ['user_api_key'] })).toBe('user_api_key');
    expect(accountKindChoice({ accountKinds: ['org_api_key', 'org_cloud'] })).toBe('custom');
    const profile = { mode: 'plan', limits: { maxSteps: 10 }, accountKinds: ['org_cloud'] } as EngineProfileNarrowing;
    expect(withAccountKinds(profile, 'user_api_key')).toEqual({ mode: 'plan', limits: { maxSteps: 10 }, accountKinds: ['user_api_key'] });
    expect(withAccountKinds(profile, 'any')).toEqual({ mode: 'plan', limits: { maxSteps: 10 } });
    expect(profile.accountKinds).toEqual(['org_cloud']);
    expect(isEngineProfile({})).toBe(true);
    expect(isEngineProfile(null)).toBe(false);
    expect(isEngineProfile(['x'])).toBe(false);
  });
});

describe('AgentAccountChoice', () => {
  it('AG-1: a labelled radio group; a kind without an available row is disabled with its reason', async () => {
    (state.access.policy as { disabledRules: string[] }).disabledRules = ['azureopenai-cloud-org'];
    state.access = {
      ...state.access,
      rules: (state.access.rules as Array<Record<string, unknown>>).filter((r) => r.id !== 'bedrock-cloud-org'),
    };
    renderChoice({});
    const group = await screen.findByRole('radiogroup', { name: 'AI account' });
    expect((within(group).getByRole('radio', { name: 'Any account the organization allows' }) as HTMLButtonElement).getAttribute('aria-checked')).toBe('true');
    expect((within(group).getByRole('radio', { name: 'Organization key' }) as HTMLButtonElement).disabled).toBe(false);
    expect((within(group).getByRole('radio', { name: 'Personal key' }) as HTMLButtonElement).disabled).toBe(false);
    const cloud = within(group).getByRole('radio', { name: 'Organization cloud account' }) as HTMLButtonElement;
    expect(cloud.disabled).toBe(true);
    expect(group.textContent).toContain('Switched off by your organization.');
  });

  it('AG-2: choosing a kind writes accountKinds and keeps the rest; Any removes it', async () => {
    const onChange = renderChoice({ mode: 'plan' });
    const group = await screen.findByRole('radiogroup', { name: 'AI account' });
    fireEvent.click(within(group).getByRole('radio', { name: 'Personal key' }));
    expect(onChange).toHaveBeenLastCalledWith({ mode: 'plan', accountKinds: ['user_api_key'] });
    cleanup();
    const second = renderChoice({ mode: 'plan', accountKinds: ['org_api_key'] });
    const again = await screen.findByRole('radiogroup', { name: 'AI account' });
    expect(within(again).getByRole('radio', { name: 'Organization key' }).getAttribute('aria-checked')).toBe('true');
    fireEvent.click(within(again).getByRole('radio', { name: 'Any account the organization allows' }));
    expect(second).toHaveBeenLastCalledWith({ mode: 'plan' });
  });

  it('AG-3: a stored list of several kinds is shown as it is, with no option selected', async () => {
    renderChoice({ accountKinds: ['org_api_key', 'user_api_key'] });
    const group = await screen.findByRole('radiogroup', { name: 'AI account' });
    expect(screen.getByText('Set to several kinds: Organization key, Personal key')).toBeTruthy();
    for (const radio of within(group).getAllByRole('radio')) expect(radio.getAttribute('aria-checked')).toBe('false');
  });

  it('AG-6: the access view unavailable (503) says try again, offers no choice, changes nothing', async () => {
    state.fail['GET /model-accounts/access'] = { statusCode: 503, message: 'down' };
    const onChange = renderChoice({ accountKinds: ['org_api_key'] });
    expect(await screen.findByText("The account options can't be loaded right now.")).toBeTruthy();
    expect(screen.queryByRole('radiogroup')).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
    delete state.fail['GET /model-accounts/access'];
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(screen.getByRole('radiogroup', { name: 'AI account' })).toBeTruthy());
  });

  it('AG-1: the release rule data decides a kind even when every row of it is engine-specific', async () => {
    state.access = { ...state.access, rules: [rule({ accountKind: 'org_api_key', engines: ['claude_code'] })] };
    renderChoice({});
    const group = await screen.findByRole('radiogroup', { name: 'AI account' });
    expect((within(group).getByRole('radio', { name: 'Organization key' }) as HTMLButtonElement).disabled).toBe(true);
    expect(group.textContent).toContain('No rule allows this.');
  });
});
