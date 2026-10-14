import { describe, it, expect, beforeEach, vi } from 'vitest';

const warning = vi.fn();
vi.mock('@/lib/store/toast-store', () => ({ toast: { warning: (...a: unknown[]) => warning(...a) } }));

let isAdmin: boolean | null = null;
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: { getState: () => ({}) },
  selectIsAdmin: () => isAdmin,
}));

const { showNoModelToast } = await import('../no-model-toast');

beforeEach(() => warning.mockReset());

describe('no AI model warning', () => {
  it('gives admins a button to the AI Models page', () => {
    isAdmin = true;
    showNoModelToast();
    const [title, options] = warning.mock.calls[0];
    expect(title).toBe('No AI model configured');
    expect(options.action).toEqual({ label: 'Open AI Models', href: '/workspace/ai-models' });
  });

  it.each([false, null])('tells members who can fix it and offers no button (isAdmin=%s)', (value) => {
    isAdmin = value;
    showNoModelToast();
    const [, options] = warning.mock.calls[0];
    expect(options.description).toMatch(/Ask a workspace admin/);
    expect(options.action).toBeUndefined();
  });
});
