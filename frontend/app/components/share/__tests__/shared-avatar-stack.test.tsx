import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { SharedAvatarStack } from '../shared-avatar-stack';

afterEach(cleanup);

const people = ['User Writer', 'Jane Reader', 'Bob Builder', 'Carol King', 'Dan Fox'].map((name, i) => ({ id: `u${i}`, name }));

const stack = (members = people) => render(<Theme><SharedAvatarStack members={members} /></Theme>);

describe('SharedAvatarStack', () => {
  it('names each avatar and rings it in the background colour', () => {
    stack(people.slice(0, 3));
    const avatars = screen.getAllByTestId('shared-avatar');
    expect(avatars.map((a) => a.getAttribute('aria-label'))).toEqual(['User Writer', 'Jane Reader', 'Bob Builder']);
    expect(avatars[0].style.border).toContain('var(--color-background)');
    expect(screen.queryByTestId('shared-avatar-overflow')).toBeNull();
  });

  it('caps at three and shows +N with the hidden names', () => {
    stack();
    expect(screen.getAllByTestId('shared-avatar')).toHaveLength(3);
    const more = screen.getByTestId('shared-avatar-overflow');
    expect(more.textContent).toBe('+2');
    expect(more.getAttribute('aria-label')).toBe('and 2 more');
    expect(screen.getByRole('group').getAttribute('aria-label')).toContain('Dan Fox');
  });
});
