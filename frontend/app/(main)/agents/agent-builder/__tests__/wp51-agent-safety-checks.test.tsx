/**
 * WP-51 (WP-31 D, GA-03, GA-04): "Safety and checks" in the agent builder. The author requires plan approval and adds
 * the checks every run's result must pass; the agent node keeps exactly those gates and the save sends them (or `{}`
 * to clear ones the agent had). A dry run is never offered here: it is a per-message choice.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';

type Node = { id: string; data: { config: Record<string, unknown> } };
const flow = vi.hoisted(() => ({ nodes: [] as Node[] }));
vi.mock('@xyflow/react', () => ({
  useReactFlow: () => ({
    setNodes: (update: (nodes: Node[]) => Node[]) => {
      flow.nodes = update(flow.nodes);
    },
  }),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { AgentSafetyChecks, checkOf } from '../components/agent-safety-checks';
import { agentGatesPayload, commandArgv } from '../../agent-gates';
import { installBrowserShims } from './agent-builder-harness';

beforeAll(async () => {
  installBrowserShims();
  await i18n.changeLanguage('en-US');
});
beforeEach(() => {
  flow.nodes = [{ id: 'core', data: { config: { systemPrompt: 'p' } } }];
});
afterEach(() => cleanup());

const saved = () => flow.nodes[0]?.data.config['gates'];

function show(stored?: unknown) {
  render(
    <Theme>
      <AgentSafetyChecks nodeId="core" stored={stored} />
    </Theme>,
  );
}

describe("the agent's own gates (WP-31 D)", () => {
  it('GA-03: "Require plan approval" holds every run to a plan; turned off, the node keeps no gate', () => {
    show();
    const plan = screen.getByRole('switch', { name: 'Require plan approval' });
    fireEvent.click(plan);
    expect(saved()).toEqual({ planApproval: 'required' });
    fireEvent.click(plan);
    expect(saved()).toBeUndefined();
    expect(screen.queryByText(/dry run/i)).toBeNull();
  });

  it('GA-04: checks are added in words and saved as the run checks them; a failed check lets the agent fix it twice', () => {
    show();
    fireEvent.click(screen.getByRole('switch', { name: 'Check results before finishing' }));
    expect(saved()).toEqual({ verify: { checks: [{ kind: 'citations' }], onFail: 'continue', maxRounds: 2 } });
    expect(screen.getByRole('combobox', { name: 'When a check fails' }).textContent).toContain(
      'Tell the agent and let it fix it (up to 2 times)',
    );
  });

  it('GA-04: a stored command check shows as its command line and keeps its time limit', () => {
    const stored = {
      verify: { checks: [{ kind: 'command', argv: ['pytest', '-q'], timeoutS: 120 }], onFail: 'flag' },
    };
    show(stored);
    expect((screen.getByRole('textbox', { name: 'Command' }) as HTMLInputElement).value).toBe('pytest -q');
    fireEvent.change(screen.getByRole('textbox', { name: 'Command' }), { target: { value: 'npm test' } });
    expect(saved()).toEqual({ verify: { checks: [{ kind: 'command', argv: ['npm', 'test'], timeoutS: 120 }], onFail: 'flag' } });
  });

  it('a path outside the workspace or a schema that is not JSON says why, and is not saved', () => {
    const row = { id: 1, path: '../secrets.txt', format: 'any', schema: '', command: '', timeoutS: '' } as const;
    expect(checkOf({ ...row, kind: 'file' })).toEqual({ problem: 'agentBuilder.gates.problem.path' });
    expect(checkOf({ ...row, kind: 'json_schema', path: 'out/a.json', schema: '{nope' })).toEqual({
      problem: 'agentBuilder.gates.problem.schema',
    });
    expect(checkOf({ ...row, kind: 'command', command: '' })).toEqual({ problem: 'agentBuilder.gates.problem.command' });
    expect(commandArgv('pytest -k "login flow"')).toEqual(['pytest', '-k', 'login flow']);
  });

  it('the save sends the edited gates, {} to clear ones the agent had, and nothing for an agent that never had any', () => {
    expect(agentGatesPayload({ planApproval: 'required' }, undefined)).toEqual({ gates: { planApproval: 'required' } });
    expect(agentGatesPayload(undefined, { planApproval: 'required' })).toEqual({ gates: {} });
    expect(agentGatesPayload(undefined, undefined)).toEqual({});
  });
});
