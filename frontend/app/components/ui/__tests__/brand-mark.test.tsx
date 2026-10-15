import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { BrandMark } from '../brand-mark';
import { BrandWordmark } from '../brand-wordmark';

describe('BrandMark', () => {
  afterEach(() => cleanup());

  it('renders the PipesHub icon by default', () => {
    const { container } = render(<BrandMark size={24} />);
    expect(container.querySelector('svg')).not.toBeNull();
  });

  it('renders what the page passes as fallback instead', () => {
    render(<BrandMark size={48} fallback={<span data-testid="page-logo" />} />);
    expect(screen.getByTestId('page-logo')).toBeTruthy();
  });
});

describe('BrandWordmark', () => {
  afterEach(() => cleanup());

  it('renders the PipesHub wordmark image', () => {
    const { container } = render(<BrandWordmark />);
    const img = container.querySelector('img');
    expect(img?.getAttribute('src')).toContain('white-horizontal.svg');
    expect(img?.getAttribute('alt')).toBe('Pipeshub');
  });
});
