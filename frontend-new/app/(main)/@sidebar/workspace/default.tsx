'use client';

import WorkspaceSidebar from '@/workspace/sidebar';

/**
 * Default fallback for the @sidebar/workspace slot.
 * Renders for all /workspace/* sub-routes that don't have
 * their own page in this slot (e.g. /workspace/general, /workspace/users…).
 *
 * In Next.js parallel routes, default.tsx is used when the current URL
 * can't be matched to a specific page in this slot — no dynamic [catchall] needed.
 */
export default function WorkspaceSidebarDefault() {
  return <WorkspaceSidebar />;
}
