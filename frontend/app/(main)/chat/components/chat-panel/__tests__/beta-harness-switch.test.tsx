/** BETA: the "+" menu's agent harness switch, where a chat chooses the harness, carries the Beta badge. */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { en } from '@/lib/__tests__/test-i18n';
import { PlusMenuContent } from '../plus-menu-button';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

afterEach(() => cleanup());

const menu = (harness?: React.ComponentProps<typeof PlusMenuContent>['harness']) =>
  render(
    <Theme>
      <PlusMenuContent
        onAttachFiles={vi.fn()}
        internalSearch
        webSearch
        onToggleInternalSearch={vi.fn()}
        onToggleWebSearch={vi.fn()}
        harness={harness}
      />
    </Theme>,
  );

describe('the "+" menu marks the agent harness Beta', () => {
  it('shows the Beta badge on the harness switch row', () => {
    menu({ checked: true, onCheckedChange: vi.fn() });
    const toggle = screen.getByRole('switch', { name: en.chat.agentCapabilities.newHarness });
    expect(toggle.closest('[role="menuitem"]')?.textContent).toContain(en.common.beta.label);
    expect(screen.getAllByText(en.common.beta.label)).toHaveLength(1);
  });

  it('shows no badge where the harness is not offered', () => {
    menu();
    expect(screen.queryByText(en.common.beta.label)).toBeNull();
  });
});
