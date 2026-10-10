import { describe, it, expect } from 'vitest';
import type { AccessView, ConversationRole } from '../../collaboration-types';
import {
  canRegenerate,
  normalizeAccessView,
  toConversationAccess,
  withCollaboratorCount,
  OWNER_ACCESS_VIEW,
} from '../conversation-access';

function view(role: ConversationRole, o: Partial<AccessView> = {}): AccessView {
  return {
    role,
    isOwner: role === 'owner',
    accessLevel: role,
    canSend: role !== 'read',
    canManage: role === 'owner',
    canInvite: role === 'owner',
    isCollaborative: true,
    ...o,
  };
}

describe('toConversationAccess', () => {
  it('flag off follows today: send unless known non-owner, manage only for the owner', () => {
    const cases: Array<[boolean | null, boolean, boolean]> = [
      [null, true, false],
      [true, true, true],
      [false, false, false],
    ];
    for (const [isOwner, canSend, canManage] of cases) {
      const a = toConversationAccess(view('write', { canInvite: true }), isOwner, false);
      expect(a).toMatchObject({ isOwner, canSend, canManage, canShare: canManage, canInvite: false });
      expect(a.isReadOnly).toBe(false);
      expect(a.isCollaborative).toBe(false);
      expect(a.role).toBeNull();
    }
  });

  it('flag on with no view yet falls back to the legacy rule', () => {
    expect(toConversationAccess(null, null, true)).toMatchObject({ canSend: true, canManage: false });
    expect(toConversationAccess(undefined, false, true)).toMatchObject({ canSend: false });
  });

  it('flag on mirrors the server view for each role', () => {
    expect(toConversationAccess(view('owner'), true, true)).toMatchObject({
      isOwner: true, role: 'owner', canSend: true, canManage: true, canShare: true, isReadOnly: false,
    });
    expect(toConversationAccess(view('write'), false, true)).toMatchObject({
      isOwner: false, role: 'write', canSend: true, canManage: false, canInvite: false, canShare: false, isReadOnly: false,
    });
    expect(toConversationAccess(view('write', { canInvite: true }), false, true)).toMatchObject({
      canShare: true, canManage: false, canInvite: true,
    });
    expect(toConversationAccess(view('read'), false, true)).toMatchObject({
      canSend: false, canShare: false, isReadOnly: true,
    });
  });

  it('never grants more than the server says', () => {
    const a = toConversationAccess(view('write', { canSend: false }), false, true);
    expect(a.canSend).toBe(false);
    expect(a.isReadOnly).toBe(false);
  });
});

describe('normalizeAccessView', () => {
  it('returns null without access', () => {
    expect(normalizeAccessView(undefined)).toBeNull();
    expect(normalizeAccessView(null)).toBeNull();
  });

  it('keeps a full view unchanged', () => {
    const v = view('write', { canInvite: true });
    expect(normalizeAccessView(v)).toBe(v);
  });

  it('fills a legacy pair with owner-only capabilities', () => {
    expect(normalizeAccessView({ isOwner: true, accessLevel: 'owner' })).toEqual({
      role: 'owner', isOwner: true, accessLevel: 'owner', canSend: true, canManage: true, canInvite: false, isCollaborative: false,
    });
    expect(normalizeAccessView({ isOwner: false, accessLevel: 'write' })).toMatchObject({
      role: 'write', canSend: false, canManage: false,
    });
    expect(normalizeAccessView({ isOwner: false })).toMatchObject({ role: 'read' });
  });

  it('treats a partial new view as legacy', () => {
    expect(normalizeAccessView({ isOwner: false, accessLevel: 'read', canSend: true })).toMatchObject({
      canSend: false,
    });
  });

  it('OWNER_ACCESS_VIEW is a solo owner', () => {
    expect(OWNER_ACCESS_VIEW).toMatchObject({ isOwner: true, canSend: true, isCollaborative: false });
  });
});

describe('canRegenerate', () => {
  const sender = { canSend: true };
  const bob = { userId: 'bob', displayName: 'Bob' };

  it('is true only for the asker, the owner included', () => {
    expect(canRegenerate(sender, { requestedBy: bob }, 'bob')).toBe(true);
    expect(canRegenerate(sender, { requestedBy: bob }, 'alice')).toBe(false);
    expect(canRegenerate(sender, { author: bob }, 'bob')).toBe(true);
    expect(canRegenerate(sender, { requestedBy: bob, author: { userId: 'alice', displayName: 'A' } }, 'bob')).toBe(true);
  });

  it('is false for a viewer, an unknown asker or an unknown me', () => {
    expect(canRegenerate({ canSend: false }, { requestedBy: bob }, 'bob')).toBe(false);
    expect(canRegenerate(sender, {}, 'bob')).toBe(false);
    expect(canRegenerate(sender, { requestedBy: null }, 'bob')).toBe(false);
    expect(canRegenerate(sender, { requestedBy: bob }, null)).toBe(false);
    expect(canRegenerate(sender, { requestedBy: bob }, undefined)).toBe(false);
  });
});

describe('withCollaboratorCount', () => {
  it('turns a solo chat collaborative once someone is added, and back when the last person is removed', () => {
    const solo = view('owner', { isCollaborative: false });
    const shared = withCollaboratorCount(solo, 1);
    expect(shared).toEqual({ ...solo, isCollaborative: true });
    expect(withCollaboratorCount(shared, 0)).toEqual(solo);
  });

  it('returns the same object when nothing changes, and passes a missing view through', () => {
    const shared = view('owner', { isCollaborative: true });
    expect(withCollaboratorCount(shared, 2)).toBe(shared);
    expect(withCollaboratorCount(null, 2)).toBeNull();
    expect(withCollaboratorCount(undefined, 2)).toBeUndefined();
  });
});
