'use client';

import WorkspaceSidebar from '@/workspace/sidebar';

/**
 * Sidebar slot for /workspace/archived-chats.
 *
 * Without this file, hard-navigating directly to /workspace/archived-chats
 * cannot match the @sidebar parallel route slot so Next.js falls back to
 * the top-level @sidebar/default.tsx which renders null.
 */
export default function ArchivedChatsSidebarSlot() {
  return <WorkspaceSidebar />;
}
