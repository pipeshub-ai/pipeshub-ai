import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

const state = vi.hoisted(() => ({ mobile: false, path: '/chat/' }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => state.mobile }));
vi.mock('next/navigation', () => ({ usePathname: () => state.path }));

import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { useNavHidden } from '../use-nav-hidden';

afterEach(() => {
  useSidebarWidthStore.getState().setNavCollapsed(false);
  state.mobile = false;
  state.path = '/chat/';
});

describe('useNavHidden', () => {
  it('hides the collapsed nav on desktop app pages', () => {
    useSidebarWidthStore.getState().setNavCollapsed(true);
    expect(renderHook(() => useNavHidden()).result.current).toBe(true);
  });

  it('keeps the workspace settings navigation even when collapsed', () => {
    useSidebarWidthStore.getState().setNavCollapsed(true);
    state.path = '/workspace/mail/';
    expect(renderHook(() => useNavHidden()).result.current).toBe(false);
  });

  it('never hides on mobile or when expanded', () => {
    expect(renderHook(() => useNavHidden()).result.current).toBe(false);
    useSidebarWidthStore.getState().setNavCollapsed(true);
    state.mobile = true;
    expect(renderHook(() => useNavHidden()).result.current).toBe(false);
  });
});
