import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { IndexActionButton } from '../index-action-button';

afterEach(() => {
  cleanup();
});

// The real MaterialIcon renders its icon name as text, so it is not mocked here.
describe('IndexActionButton', () => {
  it('is named by its label, not the icon name', () => {
    render(<IndexActionButton label="Reindex failed (3)" icon="error_outline" color="orange" iconColor="red" />);

    expect(screen.getByRole('button', { name: 'Reindex failed (3)' })).toBeTruthy();
  });

  it('uses the fuller aria label when given', () => {
    render(
      <IndexActionButton
        label="Retry"
        ariaLabel="Retry failed classification (2)"
        icon="replay"
        color="orange"
        iconColor="red"
      />
    );

    expect(screen.getByRole('button', { name: 'Retry failed classification (2)' })).toBeTruthy();
  });
});
