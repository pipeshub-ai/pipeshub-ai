import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { useAuthStore } from '@/lib/store/auth-store';

// A reload restores only the login tokens, so the id comes from the access token, not `user`.
vi.mock('@/lib/utils/jwt', () => ({ getUserIdFromToken: () => 'me' }));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));

import { ChatResponse } from '../chat-response';
import { useChatStore } from '../../../store';

beforeEach(() => {
  installBrowserShims();
  useAuthStore.setState({ user: null });
});

afterEach(() => {
  cleanup();
  useAuthStore.setState({ user: null });
});

const regenerate = () => screen.queryByRole('button', { name: 'Regenerate' });

describe('regenerate on a shared conversation', () => {
  it('is offered on the answer generated for the signed-in user, known only from the token after a reload', () => {
    renderInTheme(<ChatResponse question="Q" answer="A" messageId="m1" isLastMessage authorUserId="me" />);
    expect(regenerate()).toBeTruthy();
  });

  it('is offered on an answer with no author (the owner) when the user is not known to be a guest', () => {
    renderInTheme(<ChatResponse question="Q" answer="A" messageId="m1" isLastMessage />);
    expect(regenerate()).toBeTruthy();
  });

  it('goes away when the user is moved to Can view, even on their own answer', () => {
    useChatStore.setState({ activeSlotId: 's', slots: { s: { canWrite: false, isOwner: false } } } as never);
    renderInTheme(<ChatResponse question="Q" answer="A" messageId="m1" isLastMessage authorUserId="me" />);
    expect(regenerate()).toBeNull();
    useChatStore.setState({ activeSlotId: null, slots: {} } as never);
  });

  it('is not offered on an answer generated for someone else', () => {
    renderInTheme(<ChatResponse question="Q" answer="A" messageId="m1" isLastMessage authorUserId="someone-else" />);
    expect(regenerate()).toBeNull();
  });
});

describe('who asked', () => {
  it('names the asker above a question somebody else asked', () => {
    renderInTheme(<ChatResponse question="Q" answer="A" askedBy={{ userId: 'u2', name: 'Ben Cho' }} />);
    expect(screen.getByTestId('asked-by').textContent).toBe('Ben Cho asked');
  });

  it('says another user asked when the name is unknown, and shows nothing on the viewer’s own question', () => {
    const { unmount } = renderInTheme(<ChatResponse question="Q" answer="A" askedBy={{ userId: 'u2', name: '' }} />);
    expect(screen.getByTestId('asked-by').textContent).toBe('Another user asked');
    unmount();
    renderInTheme(<ChatResponse question="Q" answer="A" />);
    expect(screen.queryByTestId('asked-by')).toBeNull();
  });
});
