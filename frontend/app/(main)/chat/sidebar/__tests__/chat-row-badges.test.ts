import { describe, it, expect } from 'vitest';
import { chatRowBadges } from '../chat-row-badges';
import type { AccessView } from '@/chat/collaboration-types';

const access = (over: Partial<AccessView>): AccessView => ({
  role: 'write', isOwner: false, accessLevel: 'write', canSend: true, canManage: false, canInvite: false, isCollaborative: true, ...over,
});

describe('chatRowBadges', () => {
  it('is empty with the flag off', () => {
    expect(chatRowBadges({ access: access({}), isOwner: false, unreadCount: 5, collaboratorCount: 2 }, false)).toEqual({ role: null, collaborators: 0, unread: 0, muted: false });
  });
  it('maps role, clamps negatives and hides collaborator count from non-owners', () => {
    expect(chatRowBadges({ access: access({ role: 'read' }), isOwner: false, unreadCount: -1, collaboratorCount: 3 }, true)).toEqual({ role: 'read', collaborators: 0, unread: 0, muted: false });
  });
  it('shows the collaborator count to the owner and no role', () => {
    expect(chatRowBadges({ access: access({ isOwner: true, role: 'owner' }), isOwner: true, collaboratorCount: 4 }, true)).toEqual({ role: null, collaborators: 4, unread: 0, muted: false });
  });
});
