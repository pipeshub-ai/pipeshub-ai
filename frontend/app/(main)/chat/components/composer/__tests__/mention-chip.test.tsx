import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { MentionChip } from '../mention-chip';

afterEach(cleanup);

describe('MentionChip', () => {
  it('shows @label as text', () => {
    render(<MentionChip type="user" label="Bob Builder" />);
    expect(screen.getByTestId('mention-chip').textContent).toBe('@Bob Builder');
    expect(screen.getByTestId('mention-chip').getAttribute('data-mention-type')).toBe('user');
  });

  it('MN-16: a directory miss renders "Unknown user" and does not throw', () => {
    render(<MentionChip type="user" label={null} />);
    expect(screen.getByTestId('mention-chip').textContent).toBe('@Unknown user');
  });

  it('names an unknown team, agent and the assistant', () => {
    const { rerender } = render(<MentionChip type="team" label="" />);
    expect(screen.getByTestId('mention-chip').textContent).toBe('@Unknown team');
    rerender(<MentionChip type="agent" />);
    expect(screen.getByTestId('mention-chip').textContent).toBe('@Unknown agent');
    rerender(<MentionChip type="assistant" />);
    expect(screen.getByTestId('mention-chip').textContent).toBe('@Assistant');
  });

  it('never interprets a label as markup', () => {
    render(<MentionChip type="user" label={'<img src=x onerror=alert(1)>'} />);
    expect(screen.getByTestId('mention-chip').querySelector('img')).toBeNull();
    expect(screen.getByTestId('mention-chip').textContent).toBe('@<img src=x onerror=alert(1)>');
  });
});
