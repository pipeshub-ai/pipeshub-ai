import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import { en } from '@/lib/__tests__/test-i18n';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => true }));
// A touch screen: no hover, no hardware keyboard shortcuts.
vi.mock('@/lib/hooks/use-can-hover', () => ({ useCanHover: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/chat/api', () => ({
  ChatApi: { fetchConversations: vi.fn().mockResolvedValue({ conversations: [] }) },
}));

import { ChatSearch } from '../search-view';

afterEach(() => cleanup());

describe('ChatSearch on a phone', () => {
  it('has a close button', () => {
    const onClose = vi.fn();
    render(<ChatSearch open onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: en.common.close }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('hides the keyboard shortcut hint on touch screens', () => {
    render(<ChatSearch open onClose={vi.fn()} />);
    expect(screen.getByRole('button', { name: en.chat.newChat })).toBeTruthy();
    expect(screen.queryByText(/\+ K$/)).toBeNull();
  });
});
