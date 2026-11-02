import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import { en } from '@/lib/__tests__/test-i18n';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => true }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { WorkspaceRightPanel } from '../workspace-right-panel';

afterEach(() => cleanup());

describe('WorkspaceRightPanel on mobile', () => {
  it('covers the whole screen and labels its close button', () => {
    render(
      <Theme>
        <WorkspaceRightPanel open onOpenChange={vi.fn()} title="Panel">
          <span>body</span>
        </WorkspaceRightPanel>
      </Theme>,
    );
    const panel = screen.getByRole('dialog');
    expect(panel.style.inset).toMatch(/^0(px)?( 0(px)?){0,3}$/);
    expect(panel.style.width).toBe('100%');
    expect(screen.getByRole('button', { name: en.common.close })).toBeTruthy();
  });
});
