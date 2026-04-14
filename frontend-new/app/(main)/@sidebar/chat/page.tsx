'use client';

import { Suspense } from 'react';
import ChatSidebar from '../../chat/sidebar';

export default function ChatSidebarSlot() {
  return (
    <Suspense>
      <ChatSidebar />
    </Suspense>
  );
}
