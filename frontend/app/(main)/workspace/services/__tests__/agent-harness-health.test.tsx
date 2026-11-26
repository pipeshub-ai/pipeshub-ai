/**
 * Round 11 item A (OP-11): the Services page shows what the harness health route says, in plain words.
 *
 * R11-SVC-1 a deployment with the harness off shows no agent-runs row.
 * R11-SVC-2 available: the row says Healthy and shows no reason.
 * R11-SVC-3 unavailable: the row says Unavailable with the reason in the reader's language, never the reason code
 *           or raw JSON; a reason this build does not know shows the server's own sentence.
 * R11-SVC-4 a malformed or missing health answer shows no row.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import { AgentHarnessHealthRow, agentHarnessHealthOf } from '../agent-harness-health';

vi.mock('react-i18next', async () => {
  const { default: locale } = await import('@/lib/i18n/locales/en-US.json');
  return {
    useTranslation: () => ({
      i18n: { language: 'en-US' },
      t: (key: string) => {
        let cur: unknown = locale;
        for (const part of key.split('.')) {
          cur = (cur as Record<string, unknown>)?.[part];
        }
        return typeof cur === 'string' ? cur : key;
      },
    }),
    initReactI18next: { type: '3rdParty', init: () => undefined },
  };
});

function renderRow(raw: unknown) {
  return render(
    <Theme>
      <AgentHarnessHealthRow health={agentHarnessHealthOf(raw)} />
    </Theme>,
  );
}

afterEach(cleanup);

describe('Services page: agent runs health (R11-SVC)', () => {
  it('R11-SVC-1: harness off shows no row', () => {
    const { container } = renderRow({ status: 'off' });
    expect(container.textContent).toBe('');
  });

  it('R11-SVC-2: available shows Healthy and no reason', () => {
    renderRow({ status: 'available' });
    expect(screen.getByText('Agent runs')).toBeTruthy();
    expect(screen.getByText('Healthy')).toBeTruthy();
    expect(screen.queryByText(/MongoDB/)).toBeNull();
  });

  it('R11-SVC-3: unavailable shows the reason in plain words, never the code or JSON', () => {
    const { container } = renderRow({ status: 'unavailable', reason: 'arbiter', detail: 'server words' });
    expect(screen.getByText('Unavailable')).toBeTruthy();
    expect(container.textContent).toMatch(/arbiter/i);
    expect(container.textContent).toMatch(/three data-bearing members/);
    expect(container.textContent).not.toContain('server words');
    expect(container.textContent).not.toMatch(/[{}"]/);
    for (const reason of ['standalone', 'majority_unavailable', 'probe_failed', 'preparation_failed']) {
      cleanup();
      const { container: c } = renderRow({ status: 'unavailable', reason, detail: '' });
      expect(c.textContent).not.toContain(reason);
      expect(c.textContent).not.toContain('workspace.services');
    }
  });

  it('R11-SVC-3: a reason this build does not know shows the server sentence', () => {
    renderRow({ status: 'unavailable', reason: 'new_reason', detail: 'MongoDB is doing something new.' });
    expect(screen.getByText('MongoDB is doing something new.')).toBeTruthy();
  });

  it('R11-SVC-4: a missing or malformed answer shows no row', () => {
    for (const raw of [undefined, null, 'available', { status: 'weird' }]) {
      cleanup();
      const { container } = renderRow(raw);
      expect(container.textContent).toBe('');
    }
  });
});
