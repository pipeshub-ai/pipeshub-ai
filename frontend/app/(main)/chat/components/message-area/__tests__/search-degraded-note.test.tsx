import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { SearchDegradedNote } from '../search-degraded-note';

afterEach(() => cleanup());

describe('SearchDegradedNote', () => {
  it('is a quiet status line in plain words', () => {
    render(<Theme><SearchDegradedNote /></Theme>);

    expect(screen.getByRole('status').textContent).toContain('Results may be incomplete; the search service was busy.');
  });
});
