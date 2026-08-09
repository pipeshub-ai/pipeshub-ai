'use client';

import { Suspense } from 'react';
import ChatSidebar from '../../chat/sidebar';

/**
 * Sidebar slot for /workflows route — without this, the @sidebar parallel
 * route falls back to default.tsx (renders null) on a hard refresh, so the
 * nav rail (visible during client-side navigation via slot state carryover)
 * disappears. Reuse ChatSidebar so /workflows gets the same persistent nav
 * as /chat.
 */
export default function WorkflowsSidebarSlot() {
  return (
    <Suspense>
      <ChatSidebar />
    </Suspense>
  );
}
