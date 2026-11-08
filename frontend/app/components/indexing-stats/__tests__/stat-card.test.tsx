import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { StatCard } from '../stat-card';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

afterEach(() => {
  cleanup();
});

describe('StatCard', () => {
  it('is not a control when it has no click handler', () => {
    render(<StatCard label="Indexed" value={4} subtitle="records" />);

    expect(screen.queryByRole('button')).toBeNull();
  });

  it('opens from the keyboard with Enter and Space when it is clickable', () => {
    const onClick = vi.fn();
    render(<StatCard label="Password protected" value={2} subtitle="records" onClick={onClick} />);

    const card = screen.getByRole('button', { name: 'Password protected 2 records' });
    fireEvent.keyDown(card, { key: 'Enter' });
    fireEvent.keyDown(card, { key: ' ' });
    fireEvent.keyDown(card, { key: 'a' });

    expect(card.tabIndex).toBe(0);
    expect(onClick).toHaveBeenCalledTimes(2);
  });
});
