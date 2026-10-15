import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { SidebarBase } from '../sidebar-base';

afterEach(cleanup);

const ui = (isMobile: boolean) => (
  <Theme>
    <SidebarBase isMobile={isMobile} mobileOpen onMobileClose={() => {}} header={<span>head</span>}>
      <span>body</span>
    </SidebarBase>
  </Theme>
);

describe('SidebarBase hook order', () => {
  it('survives isMobile flipping from false to true (useIsMobile starts false, then flips)', () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { rerender } = render(ui(false));
    expect(screen.getByText('body')).toBeTruthy();
    expect(() => rerender(ui(true))).not.toThrow();
    expect(screen.getByText('body')).toBeTruthy();
    expect(() => rerender(ui(false))).not.toThrow();
    errors.mockRestore();
  });
});
