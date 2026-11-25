import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

/**
 * R50-OFF (ADR-OPS-06): with the agent harness switched off the server answers no rollout (`agentHarness: null`),
 * and no harness page renders, calls its routes or stays reachable by its address.
 */

const router = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: router.replace, back: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/workspace/ai-access/',
}));
vi.mock('@/app/(main)/workspace/ai-access/components/org-ai-access', () => ({
  OrgAiAccess: () => <div data-testid="harness-page">ai access</div>,
}));
vi.mock('@/app/(main)/workspace/ai-accounts/components/my-ai-accounts', () => ({
  MyAiAccounts: () => <div data-testid="harness-page">ai accounts</div>,
}));

const { useFeatureFlagsStore, selectAgentHarnessOn, selectAgentHarnessEnabled } = await import(
  '@/lib/store/feature-flags-store'
);
const { HarnessOnly } = await import('../harness-only');
const pages = {
  'AI access': (await import('@/app/(main)/workspace/ai-access/page')).default,
  'AI accounts': (await import('@/app/(main)/workspace/ai-accounts/page')).default,
};

const offAnswer = { flags: { ENABLE_MCP: true }, agentHarness: null, readFailed: false } as never;
const onAnswer = {
  flags: { ENABLE_MCP: true },
  agentHarness: { useHarness: true, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true },
  readFailed: false,
} as never;

beforeEach(() => router.replace.mockReset());
afterEach(() => cleanup());

describe('the harness switch in the browser', () => {
  it('reads the server’s answer for the org: no rollout, or one with the org off, is off (ADR-OPS-08)', () => {
    expect(selectAgentHarnessOn({ agentHarness: null })).toBe(false);
    const orgOff = { agentHarness: { useHarness: false, reason: 'org_disabled', resumeInFlightRuns: false, harnessOn: false } } as never;
    expect(selectAgentHarnessOn(orgOff)).toBe(false);
    expect(selectAgentHarnessEnabled(orgOff)).toBe(false);
  });

  it('renders nothing of a harness page before the flags answer, and does not send the reader away yet', () => {
    useFeatureFlagsStore.setState({ flags: null, agentHarness: null, readFailed: false } as never);
    render(<HarnessOnly><div data-testid="inside" /></HarnessOnly>);
    expect(screen.queryByTestId('inside')).toBeNull();
    expect(router.replace).not.toHaveBeenCalled();
  });

  for (const [name, Page] of Object.entries(pages)) {
    it(`${name}: switched off, renders nothing and sends the reader to General`, () => {
      useFeatureFlagsStore.setState(offAnswer);
      render(<Page />);
      expect(screen.queryByTestId('harness-page')).toBeNull();
      expect(router.replace).toHaveBeenCalledWith('/workspace/general');
    });

    it(`${name}: switched on for the workspace, renders as before`, () => {
      useFeatureFlagsStore.setState(onAnswer);
      render(<Page />);
      expect(screen.getByTestId('harness-page')).toBeTruthy();
      expect(router.replace).not.toHaveBeenCalled();
    });

    for (const [answer, reason, resume] of [['kill switch', 'kill_switch', true], ['infrastructure not ready', 'infrastructure_unavailable', false]] as const) {
      it(`${name}: ${answer} with Labs on, still renders: it stops new runs, not the page (ADR-OPS-08)`, () => {
        useFeatureFlagsStore.setState({
          flags: { ENABLE_MCP: true },
          agentHarness: { useHarness: false, reason, resumeInFlightRuns: resume, harnessOn: true },
          readFailed: false,
        } as never);
        render(<Page />);
        expect(screen.getByTestId('harness-page')).toBeTruthy();
        expect(router.replace).not.toHaveBeenCalled();
      });
    }
  }
});
