'use client';

import { Suspense } from 'react';
import ChatSidebar from '../../chat/sidebar';

/** The explorer keeps the chat sidebar, like /artifacts: without a slot a reload or a link into /graph shows none. */
export default function GraphSidebarSlot() {
  return (
    <Suspense>
      <ChatSidebar />
    </Suspense>
  );
}
