import { describe, expect, it } from 'vitest';
import { useMobileSidebarStore } from '../mobile-sidebar-store';

describe('useMobileSidebarStore registration', () => {
  it('counts a registered sidebar until it unregisters', () => {
    const unregister = useMobileSidebarStore.getState().register();
    expect(useMobileSidebarStore.getState().registeredCount).toBe(1);

    unregister();
    unregister(); // a second call must not go negative
    expect(useMobileSidebarStore.getState().registeredCount).toBe(0);
  });
});
