/**
 * QAFINAL-FU-UX: the model access table on a phone. The UX capture of AI access at 390 px showed the Allowed switch
 * past the right edge, behind a sideways scroll; on a phone the account and engines fold under the provider, so the
 * three columns left (provider, status, the switch) fit.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const mobile = vi.hoisted(() => ({ value: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => mobile.value, useIsBelowDesktop: () => mobile.value }));

import { AccessRulesTable } from '../components/access-rules-table';
import type { AccessRule } from '../types';

const RULE = {
  id: 'openai-api-key-user',
  provider: 'openai',
  accountKind: 'user_api_key',
  route: 'gateway',
  engines: null,
  status: 'allowed',
  consumer: false,
  source: '',
  built: true,
  outcome: 'allowed',
} as unknown as AccessRule;

function renderTable() {
  render(
    <Theme>
      <AccessRulesTable rules={[RULE]} label="Model access" controlHeader="Allowed" control={() => <button type="button">Allow</button>} />
    </Theme>,
  );
  return screen.getByRole('table', { name: 'Model access' });
}

afterEach(() => cleanup());

describe('the model access table', () => {
  it('ACCESSTBL-1: on a desktop each fact has its column', () => {
    mobile.value = false;
    const table = renderTable();
    expect(within(table).getAllByRole('columnheader').map((c) => c.textContent)).toEqual(['Provider', 'Account', 'Engines', 'Status', 'Allowed']);
  });

  it("ACCESSTBL-2: on a phone the account and engines fold under the provider, and the row's switch keeps its column", () => {
    mobile.value = true;
    const table = renderTable();
    expect(within(table).getAllByRole('columnheader').map((c) => c.textContent)).toEqual(['Provider', 'Status', 'Allowed']);
    const header = within(table).getByRole('rowheader');
    expect(header.textContent).toContain('OpenAI');
    expect(header.textContent).toContain('Engines: All');
    expect(within(table).getByRole('button', { name: 'Allow' })).toBeTruthy();
  });
});
