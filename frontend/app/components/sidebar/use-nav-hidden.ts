'use client';

import { usePathname } from 'next/navigation';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';

/**
 * Whether the desktop nav sidebar is collapsed out of view. Workspace settings always keep their
 * navigation: the collapse toggle belongs to the app sidebar, and a settings page without its
 * menu left only the expand button.
 */
export function useNavHidden(): boolean {
  // In the drawer layout the sidebar is a drawer, never collapsed in place.
  const isDrawerLayout = useIsBelowDesktop();
  const isNavCollapsed = useSidebarWidthStore((s) => s.isNavCollapsed);
  const pathname = usePathname() ?? '';
  return !isDrawerLayout && isNavCollapsed && !pathname.startsWith('/workspace');
}
