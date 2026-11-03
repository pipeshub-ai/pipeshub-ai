import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { FullNameDialog } from '../full-name-dialog';

afterEach(() => cleanup());

describe('FullNameDialog', () => {
  it('shrinks below its desktop width instead of forcing a minimum', () => {
    render(
      <Theme>
        <FullNameDialog open onSuccess={vi.fn()} />
      </Theme>,
    );
    const content = screen.getByRole('dialog');
    expect(content.style.minWidth).toBe('');
    expect(content.style.width).toContain('100vw');
  });
});
