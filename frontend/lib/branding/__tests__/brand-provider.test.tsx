import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { BrandProvider, DEFAULT_BRAND, useBrand } from '@/lib/branding';

function ProductName() {
  return <span data-testid="product">{useBrand().productName}</span>;
}

describe('BrandProvider', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('uses the default brand and makes no request without loadBrand', () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    document.title = 'Pipeshub AI';

    render(
      <BrandProvider>
        <ProductName />
      </BrandProvider>,
    );

    expect(screen.getByTestId('product').textContent).toBe('PipesHub');
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(document.title).toBe('Pipeshub AI');
  });

  it('gives the default brand outside a provider', () => {
    render(<ProductName />);
    expect(screen.getByTestId('product').textContent).toBe('PipesHub');
  });

  it('uses the brand loadBrand returns, and its name as the page title', async () => {
    const loadBrand = vi.fn().mockResolvedValue({ ...DEFAULT_BRAND, productName: 'Acme Search' });

    render(
      <BrandProvider loadBrand={loadBrand}>
        <ProductName />
      </BrandProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('product').textContent).toBe('Acme Search'));
    expect(loadBrand).toHaveBeenCalledTimes(1);
    expect(document.title).toBe('Acme Search');
  });
});
