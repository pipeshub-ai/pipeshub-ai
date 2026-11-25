/**
 * QAFIX-7: `/status` on a loop run that has no control-plane pin says what applies ("Model default", "Agent loop",
 * "None asked"), and a dash only for what is not known.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../../activity-panel/activity-panel-host', () => ({ ViewActivityButton: () => null }));

const { BuiltinResultRow } = await import('../builtin-result-rows');
const { statusFromRun } = await import('../../../builtin-actions');

afterEach(() => cleanup());

const show = (status: ReturnType<typeof statusFromRun>) =>
  render(
    <Theme>
      <BuiltinResultRow rowKey="slot-1" row={{ id: 'r1', typed: '/status', afterPairs: 1, result: { kind: 'status', status, run: null } }} />
    </Theme>,
  );

describe('/status row (QAFIX-7)', () => {
  it('a loop run without a pin: real values, no bare dash', () => {
    show(statusFromRun(null, {
      status: 'completed', model: { provider: 'openai', model: 'e2e-llm' }, budget: { committed: { tokens: 30 } } as never, approvals: [],
      executor: 'loop', reasoningEffort: null, toolsGranted: 4,
    }));
    const row = screen.getByTestId('builtin-result-row').textContent ?? '';
    expect(row).toContain('Model default');
    expect(row).toContain('Agent loop');
    expect(row).toContain('None asked');
    expect(row).toContain('4');
    expect(row).not.toContain('—');
  });

  it('an engine run names its engine as its cards do; asked approvals are counted', () => {
    show(statusFromRun(null, {
      status: 'completed', budget: null, approvals: [{ interactionId: 'ix', status: 'approved' }] as never,
      executor: 'codex', reasoningEffort: 'high', toolsGranted: 1,
    }));
    const row = screen.getByTestId('builtin-result-row').textContent ?? '';
    expect(row).toContain('Codex');
    expect(row).toContain('1 asked in this reply');
  });

  it("CMDUI: a loop run names the coding engine its code tasks go to, or None, so a chat's /engine shows as taken", () => {
    show(statusFromRun(null, { status: 'completed', budget: null, approvals: [], executor: 'loop', reasoningEffort: null, toolsGranted: 3, codingEngine: 'opencode' }));
    let row = screen.getByTestId('builtin-result-row').textContent ?? '';
    expect(row).toContain('Agent loop');
    expect(row).toContain('Coding engine');
    expect(row).toContain('opencode');
    cleanup();
    show(statusFromRun(null, { status: 'completed', budget: null, approvals: [], executor: 'loop', reasoningEffort: null, toolsGranted: 3, codingEngine: null }));
    row = screen.getByTestId('builtin-result-row').textContent ?? '';
    expect(row).toContain('Coding engineNone');
    cleanup();
    // An engine run is its engine: the executor line already names it.
    show(statusFromRun(null, { status: 'completed', budget: null, approvals: [], executor: 'codex', reasoningEffort: null, toolsGranted: 1, codingEngine: null }));
    expect(screen.getByTestId('builtin-result-row').textContent ?? '').not.toContain('Coding engine');
  });

  it('C4.04: a pinned run shows its settings version (the pin digest, shortened); a run without a pin has no such row', () => {
    const pinned = {
      resolved: {
        digest: 'sha256:0f3a9c4e1b2d7a6e5f40c1d2e3f4a5b6c7d8e9f00112233445566778899aabbcc',
        llm: { model: 'gpt-5.2' },
        effectiveSet: { items: [] },
        effectiveConfig: { schemaVersion: 1, dimensions: {} },
      },
    } as never;
    show(statusFromRun(pinned, { status: 'completed', budget: null, executor: 'loop', reasoningEffort: null }));
    const row = screen.getByTestId('builtin-result-row').textContent ?? '';
    expect(row).toContain('Settings version0f3a9c4e1b2d');
    expect(row).not.toContain('sha256');
    cleanup();
    show(statusFromRun(null, { status: 'completed', budget: null, executor: 'loop', reasoningEffort: null }));
    expect(screen.getByTestId('builtin-result-row').textContent ?? '').not.toContain('Settings version');
  });
});
