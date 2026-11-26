'use client';

import { Suspense } from 'react';
import ChatSidebar from '../../chat/sidebar';

export default function JobsSidebarSlot() {
  return (
    <Suspense>
      <ChatSidebar />
    </Suspense>
  );
}
