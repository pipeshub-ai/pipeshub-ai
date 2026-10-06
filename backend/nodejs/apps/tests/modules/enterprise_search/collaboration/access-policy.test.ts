import { expect } from 'chai';
import {
  OPERATION_REQUIREMENTS,
  decide,
  resolveRole,
  toAccessView,
} from '../../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.policy';
import { COLLAB_ERROR_CODES } from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors';
import {
  Caller,
  ConversationAccessFields,
  ConversationOperation,
  ConversationRole,
} from '../../../../src/modules/enterprise_search/services/collaboration/domain/types';

const OPS = Object.keys(OPERATION_REQUIREMENTS) as ConversationOperation[];
const ROLES: ConversationRole[] = ['owner', 'write', 'read', 'none'];
const RANK: Record<ConversationRole, number> = { none: 0, read: 1, write: 2, owner: 3 };

describe('decide(): the 51 section 2 operation table', () => {
  for (const op of OPS) {
    for (const role of ROLES) {
      for (const editorsCanInvite of [false, true]) {
        for (const ownQuestion of [false, true]) {
          it(`${op} / ${role} / editorsCanInvite=${editorsCanInvite} / ownQuestion=${ownQuestion}`, () => {
            const req = OPERATION_REQUIREMENTS[op];
            const d = decide(op, role, {
              editorsCanInvite,
              isAuthorOfAnsweredQuestion: ownQuestion,
              isRequester: ownQuestion,
            });
            if (role === 'none') {
              expect(d).to.deep.equal({ allowed: false, status: 404, code: COLLAB_ERROR_CODES.NOT_FOUND });
              return;
            }
            if (RANK[role] < RANK[req.min]) {
              expect(d).to.deep.equal({
                allowed: false,
                status: 403,
                code: COLLAB_ERROR_CODES[req.belowMin],
              });
              return;
            }
            const guardCode =
              (req.guard === 'nonOwner' && role === 'owner' && COLLAB_ERROR_CODES.OWNER_ONLY) ||
              (req.guard === 'editorsCanInvite' && role === 'write' && !editorsCanInvite && COLLAB_ERROR_CODES.OWNER_ONLY) ||
              (req.guard === 'asker' && !ownQuestion && COLLAB_ERROR_CODES.REGENERATE_NOT_ALLOWED) ||
              (req.guard === 'requester' && !ownQuestion && COLLAB_ERROR_CODES.RESUME_NOT_ALLOWED) ||
              undefined;
            if (guardCode) {
              expect(d).to.deep.equal({ allowed: false, status: 403, code: guardCode });
            } else {
              expect(d).to.deep.equal({ allowed: true, role });
            }
          });
        }
      }
    }
  }

  it('denies send, regenerate and resume with OWNER_INACTIVE, but not read or cancel (D11)', () => {
    const ctx = { ownerActive: false, isAuthorOfAnsweredQuestion: true, isRequester: true };
    for (const op of ['send', 'regenerate', 'resume'] as const) {
      expect(decide(op, 'write', ctx)).to.deep.include({ allowed: false, code: COLLAB_ERROR_CODES.OWNER_INACTIVE });
    }
    expect(decide('read', 'read', ctx).allowed).to.equal(true);
    expect(decide('cancel', 'write', ctx).allowed).to.equal(true);
  });

  it('treats an unknown owner state as active', () => {
    expect(decide('send', 'write', {}).allowed).to.equal(true);
  });

  it('maps unresolved teams to 503', () => {
    expect(decide('send', 'unresolved')).to.deep.equal({
      allowed: false,
      status: 503,
      code: COLLAB_ERROR_CODES.TEAM_RESOLUTION_UNAVAILABLE,
    });
  });
});

const caller = (over: Partial<Caller> = {}): Caller => ({ userId: 'u1', orgId: 'o1', teamIds: [], ...over });
const session = (over: Partial<ConversationAccessFields> = {}): ConversationAccessFields => ({
  orgId: 'o1',
  userId: 'owner',
  sharedWith: [],
  ...over,
});

describe('resolveRole()', () => {
  it('maps write rows to read while the collaboration flag is off', () => {
    const s = session({ sharedWith: [{ userId: 'u1', accessLevel: 'write' }] });
    expect(resolveRole(s, caller(), { collab: true })).to.deep.include({ role: 'write' });
    expect(resolveRole(s, caller(), { collab: false })).to.deep.include({ role: 'read' });
  });

  it('is none for a deleted conversation, even for the owner', () => {
    expect(resolveRole(session({ isDeleted: true }), caller({ userId: 'owner' }), { collab: true })).to.deep.include({
      role: 'none',
    });
  });

  it('does not consult unresolved teams when the owner or a direct row already satisfies the operation', () => {
    const c = caller({ teamIds: 'unresolved' });
    expect(resolveRole(session({ userId: 'u1' }), c, { collab: true, op: 'delete' })).to.deep.include({ role: 'owner' });
    const direct = session({ sharedWith: [{ userId: 'u1', accessLevel: 'write' }, { teamId: 't1', accessLevel: 'write' }] });
    expect(resolveRole(direct, c, { collab: true, op: 'send' })).to.deep.include({ role: 'write' });
  });

  it('is unresolved when only a team row could grant', () => {
    const s = session({ sharedWith: [{ teamId: 't1', accessLevel: 'write' }] });
    expect(resolveRole(s, caller({ teamIds: 'unresolved' }), { collab: true, op: 'send' })).to.deep.equal({
      unresolved: true,
    });
  });
});

describe('toAccessView()', () => {
  const view = (s: ConversationAccessFields, role: 'owner' | 'write' | 'read', collab = true) =>
    toAccessView(role, s, caller({ userId: role === 'owner' ? 'owner' : 'u1' }), { collab });

  it('lets the owner send, manage and invite', () => {
    expect(view(session(), 'owner')).to.include({ isOwner: true, canSend: true, canManage: true, canInvite: true, accessLevel: 'owner' });
  });

  it('lets an editor invite only when editorsCanInvite is set', () => {
    expect(view(session(), 'write').canInvite).to.equal(false);
    expect(view(session({ settings: { editorsCanInvite: true } }), 'write')).to.include({ canInvite: true, canManage: false, canSend: true });
  });

  it('never lets a reader send or invite', () => {
    expect(view(session({ settings: { editorsCanInvite: true } }), 'read')).to.include({ canSend: false, canInvite: false, canManage: false });
  });

  it('is collaborative only with the flag on and a share or project visibility', () => {
    expect(view(session(), 'owner').isCollaborative).to.equal(false);
    const shared = session({ sharedWith: [{ userId: 'u2', accessLevel: 'read' }] });
    expect(view(shared, 'owner').isCollaborative).to.equal(true);
    expect(view(shared, 'owner', false).isCollaborative).to.equal(false);
    expect(view(session({ projectVisibility: 'project' }), 'owner').isCollaborative).to.equal(true);
  });
});
