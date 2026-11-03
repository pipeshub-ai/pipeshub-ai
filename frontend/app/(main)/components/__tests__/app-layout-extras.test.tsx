import React from 'react';
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AppLayoutExtras } from '../app-layout-extras';

describe('AppLayoutExtras', () => {
  it('renders nothing by default', () => {
    const { container } = render(<AppLayoutExtras />);
    expect(container.innerHTML).toBe('');
  });
});
