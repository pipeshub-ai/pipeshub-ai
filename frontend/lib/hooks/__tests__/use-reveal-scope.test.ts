import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn() } }));

import { useRevealScope } from '../use-secret-reveal-available';

describe('useRevealScope', () => {
  it('keeps a reveal current while the form shows the same config', () => {
    const { result, rerender } = renderHook(({ scope }) => useRevealScope(scope), {
      initialProps: { scope: 'open:openai' },
    });
    const stillCurrent = result.current();

    rerender({ scope: 'open:openai' });

    expect(stillCurrent()).toBe(true);
  });

  it('drops a reveal once the form moves to another config', () => {
    const { result, rerender } = renderHook(({ scope }) => useRevealScope(scope), {
      initialProps: { scope: 'open:openai' },
    });
    const stillCurrent = result.current();

    rerender({ scope: 'open:anthropic' });

    expect(stillCurrent()).toBe(false);
  });

  it('drops a reveal when the form closes and reopens on the same config', () => {
    const { result, rerender } = renderHook(({ scope }) => useRevealScope(scope), {
      initialProps: { scope: 'true:bot-1' },
    });
    const stillCurrent = result.current();

    rerender({ scope: 'false:bot-1' });
    rerender({ scope: 'true:bot-1' });

    expect(stillCurrent()).toBe(false);
  });

  it('treats a new object with the same contents as a different config', () => {
    const { result, rerender } = renderHook(({ scope }) => useRevealScope(scope), {
      initialProps: { scope: { host: 'smtp.example.com' } as object | null },
    });
    const stillCurrent = result.current();

    rerender({ scope: { host: 'smtp.example.com' } });

    expect(stillCurrent()).toBe(false);
  });

  it('lets a reveal started after the change apply', () => {
    const { result, rerender } = renderHook(({ scope }) => useRevealScope(scope), {
      initialProps: { scope: 'a' },
    });
    rerender({ scope: 'b' });

    expect(result.current()()).toBe(true);
  });

  it('returns the same starter across renders so it is safe in effect deps', () => {
    const { result, rerender } = renderHook(({ scope }) => useRevealScope(scope), {
      initialProps: { scope: 'a' },
    });
    const first = result.current;

    rerender({ scope: 'b' });

    expect(result.current).toBe(first);
  });
});
