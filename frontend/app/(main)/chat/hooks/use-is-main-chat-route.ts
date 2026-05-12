'use client';

import { usePathname } from 'next/navigation';

/**
 * True on the main assistant chat route (`/chat` or `/chat/`), matching sidebar logic.
 * Not for URL-scoped agent chat or other app sections.
 */
export function useIsMainChatRoute(): boolean {
  const pathname = usePathname();
  return pathname === '/chat' || pathname === '/chat/';
}
