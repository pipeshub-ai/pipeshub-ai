import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { ProfileExtraSections } from '../profile-extra-sections';

afterEach(cleanup);

describe('ProfileExtraSections, the default', () => {
  it('adds no section to the end of the profile page', () => {
    const { container } = render(<ProfileExtraSections />);
    expect(container.innerHTML).toBe('');
  });
});
