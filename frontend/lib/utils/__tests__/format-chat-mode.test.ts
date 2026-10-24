import { describe, it, expect } from 'vitest';
import { formatChatMode } from '../formatters';

describe('formatChatMode', () => {
  it('reads a snake_case mode in sentence case, like the plus menu', () => {
    expect(formatChatMode('internal_search')).toBe('Internal search');
    expect(formatChatMode('web_search')).toBe('Web search');
  });

  it('is empty without a mode', () => {
    expect(formatChatMode(undefined)).toBe('');
  });
});
