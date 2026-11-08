import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import { en } from '@/lib/__tests__/test-i18n';
import nextConfig from '../../next.config.mjs';

vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));
vi.mock('@/config', () => ({ BrandMark: () => <svg data-testid="brand-mark" /> }));

import NotFound from '../(main)/not-found';

afterEach(() => cleanup());

describe('not-found page', () => {
  it('is used for unmatched URLs across both root layouts', () => {
    // Without a global not-found, Next serves its unbranded default 404.
    expect(nextConfig.experimental?.globalNotFound).toBe(true);
  });

  it('shows the brand, the message and a way back into the app', () => {
    render(<NotFound />);
    expect(screen.getByTestId('brand-mark')).toBeTruthy();
    expect(screen.getByText(en.common.errorBoundary.notFoundTitle)).toBeTruthy();
    expect(
      screen.getByRole('link', { name: en.common.errorBoundary.goToChat }).getAttribute('href'),
    ).toBe('/chat/');
  });
});
