import { describe, expect, it, vi } from 'vitest';

vi.mock('@/chat/components/chat-input', () => ({ ChatInput: () => null }));
import { ellipsize } from '../chat-widget-wrapper';

describe('ellipsize', () => {
  it('does not split a surrogate pair', () => {
    const out = ellipsize('😀'.repeat(20), 12);
    expect(Array.from(out)).toHaveLength(12);
    expect(out.endsWith('😀…')).toBe(true);
  });
  it('keeps short titles unchanged', () => {
    expect(ellipsize('Docs', 12)).toBe('Docs');
  });
});
