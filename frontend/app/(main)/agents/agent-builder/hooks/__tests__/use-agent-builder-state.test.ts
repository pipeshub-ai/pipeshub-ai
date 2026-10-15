import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useAgentBuilderState } from '../use-agent-builder-state';

const mobile = vi.hoisted(() => ({ value: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => mobile.value }));

describe('useAgentBuilderState palette default', () => {
  beforeEach(() => {
    mobile.value = false;
  });

  it('keeps the palette open on desktop', () => {
    const { result } = renderHook(() => useAgentBuilderState());
    expect(result.current.sidebarOpen).toBe(true);
    expect(result.current.isMobile).toBe(false);
  });

  it('starts with the palette closed at phone width, and it can be reopened', () => {
    mobile.value = true;
    const { result } = renderHook(() => useAgentBuilderState());
    expect(result.current.sidebarOpen).toBe(false);
    act(() => result.current.setSidebarOpen(true));
    expect(result.current.sidebarOpen).toBe(true);
  });
});
