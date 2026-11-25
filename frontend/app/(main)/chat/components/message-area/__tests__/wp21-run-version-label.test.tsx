/**
 * WP-21 (OWD-14), approved mock section 5: an agent's answer says which published version answered, as a plain
 * label in its activity line. The version is the run's own record (`agentVersion` on the run view), read once.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, renderHook, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const getAgentRun = vi.hoisted(() => vi.fn());
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: { getAgentRun } }));

import { resetRunAgentVersions, useRunAgentVersion } from '../../../hooks/use-run-agent-version';
import { RunVersionLabel } from '../run-version-label';

const RUN = { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-1' };

beforeEach(() => {
  resetRunAgentVersions();
  getAgentRun.mockResolvedValue({ runId: 'run-1', agentVersion: 4 });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('WP-21 OWD-14: which version answered', () => {
  it('OWD-14 CV-1: names the agent and the version, with no ids', () => {
    render(<Theme><RunVersionLabel agentName="Support triage" version={4} /></Theme>);
    expect(screen.getByTestId('run-agent-version').textContent).toBe('Support triage · version 4');
  });

  it('OWD-14 CV-2: without the agent’s name it still says the version; without a version it shows nothing', () => {
    const { rerender } = render(<Theme><RunVersionLabel agentName={null} version={2} /></Theme>);
    expect(screen.getByTestId('run-agent-version').textContent).toBe('Agent version 2');
    rerender(<Theme><RunVersionLabel agentName="Support triage" version={null} /></Theme>);
    expect(screen.queryByTestId('run-agent-version')).toBeNull();
  });

  it('OWD-14 CV-3: reads a finished run’s version once, however many answers ask', async () => {
    const first = renderHook(() => useRunAgentVersion(RUN, true));
    const second = renderHook(() => useRunAgentVersion(RUN, true));
    await waitFor(() => expect(first.result.current).toBe(4));
    await waitFor(() => expect(second.result.current).toBe(4));
    expect(getAgentRun).toHaveBeenCalledTimes(1);
    expect(getAgentRun).toHaveBeenCalledWith('agent-1', 'conv-1', 'run-1');
  });

  it('OWD-14 CV-4: nothing is read while the run streams, for the assistant, or for a run from before versions', async () => {
    const streaming = renderHook(() => useRunAgentVersion(RUN, false));
    const assistant = renderHook(() => useRunAgentVersion({ sessionId: 'conv-1', runId: 'run-2' }, true));
    expect(streaming.result.current).toBeNull();
    expect(assistant.result.current).toBeNull();
    expect(getAgentRun).not.toHaveBeenCalled();

    getAgentRun.mockResolvedValueOnce({ runId: 'run-3' });
    const old = renderHook(() => useRunAgentVersion({ ...RUN, runId: 'run-3' }, true));
    await waitFor(() => expect(getAgentRun).toHaveBeenCalledTimes(1));
    expect(old.result.current).toBeNull();
  });

  it('OWD-14 CV-5: a failed read shows no label and is tried again by the next answer', async () => {
    getAgentRun.mockRejectedValueOnce(new Error('down'));
    const first = renderHook(() => useRunAgentVersion(RUN, true));
    await waitFor(() => expect(getAgentRun).toHaveBeenCalledTimes(1));
    expect(first.result.current).toBeNull();
    const again = renderHook(() => useRunAgentVersion(RUN, true));
    await waitFor(() => expect(again.result.current).toBe(4));
    expect(getAgentRun).toHaveBeenCalledTimes(2);
  });
});
