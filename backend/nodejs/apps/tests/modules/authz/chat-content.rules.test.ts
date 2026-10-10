import { expect } from 'chai';
import {
  attachmentAuthorOf,
  canReadChatArtifact,
  canReadChatAttachment,
  ChatArtifactKind,
} from '../../../src/modules/authz/domain/chat-content.rules';
import { CANONICAL_ROLES, CanonicalRole } from '../../../src/modules/authz/domain/ladder';

const AUTHOR = 'u-author';
const OWNER = 'u-owner';

describe('chat-content rules (H4/H5)', () => {
  describe('canReadChatAttachment', () => {
    const readers: CanonicalRole[] = ['viewer', 'commenter', 'editor', 'manager', 'owner'];

    describe('flag on', () => {
      for (const role of CANONICAL_ROLES) {
        for (const filesShared of [true, false, undefined]) {
          const allowed = role !== 'none' && filesShared === true;
          it(`${role} x filesShared=${String(filesShared)} -> ${String(allowed)}`, () => {
            expect(
              canReadChatAttachment(role, { authorUserId: AUTHOR, sessionOwnerId: OWNER, filesShared }, true),
            ).to.equal(allowed);
          });
        }
      }

      it('denies a legacy row (no authorUserId, no filesShared)', () => {
        expect(canReadChatAttachment('owner', { sessionOwnerId: OWNER }, true)).to.equal(false);
      });
    });

    describe('flag off (parity with the retired grant-as-initiator path)', () => {
      for (const role of readers) {
        it(`${role}: the owner's own attachment is readable regardless of consent`, () => {
          expect(canReadChatAttachment(role, { authorUserId: OWNER, sessionOwnerId: OWNER }, false)).to.equal(true);
          expect(canReadChatAttachment(role, { authorUserId: OWNER, sessionOwnerId: OWNER, filesShared: false }, false)).to.equal(true);
        });
      }

      it('a legacy row reads as authored by the session owner', () => {
        expect(canReadChatAttachment('viewer', { sessionOwnerId: OWNER }, false)).to.equal(true);
      });

      it("another participant's attachment was never granted, so it stays denied even with filesShared", () => {
        expect(canReadChatAttachment('viewer', { authorUserId: AUTHOR, sessionOwnerId: OWNER, filesShared: true }, false)).to.equal(false);
      });

      it('no chat role, no read', () => {
        expect(canReadChatAttachment('none', { authorUserId: OWNER, sessionOwnerId: OWNER }, false)).to.equal(false);
      });
    });
  });

  describe('attachmentAuthorOf', () => {
    it('prefers the stamped author and falls back to the session owner', () => {
      expect(attachmentAuthorOf({ authorUserId: AUTHOR, sessionOwnerId: OWNER })).to.equal(AUTHOR);
      expect(attachmentAuthorOf({ sessionOwnerId: OWNER })).to.equal(OWNER);
    });
  });

  describe('canReadChatArtifact', () => {
    const visible: ChatArtifactKind = { visibility: 'VISIBLE', isTemporary: false };

    describe('flag on', () => {
      for (const role of CANONICAL_ROLES) {
        for (const turn of [{ shareToolResults: true }, { shareToolResults: false }, {}, null]) {
          const allowed = role !== 'none' && turn?.shareToolResults === true;
          it(`${role} x turn=${JSON.stringify(turn)} -> ${String(allowed)}`, () => {
            expect(canReadChatArtifact(role, turn, visible, true)).to.equal(allowed);
          });
        }
      }

      it('denies STAGING and temporary artifacts even with consent', () => {
        const turn = { shareToolResults: true };
        expect(canReadChatArtifact('viewer', turn, { visibility: 'STAGING' }, true)).to.equal(false);
        expect(canReadChatArtifact('viewer', turn, { isTemporary: true }, true)).to.equal(false);
        expect(canReadChatArtifact('viewer', turn, {}, true)).to.equal(true);
      });
    });

    describe('flag off', () => {
      it('ignores the turn: any chat reader gets a visible, persistent artifact', () => {
        expect(canReadChatArtifact('viewer', null, visible, false)).to.equal(true);
        expect(canReadChatArtifact('viewer', { shareToolResults: false }, visible, false)).to.equal(true);
      });

      it('still denies STAGING, temporary and no role', () => {
        expect(canReadChatArtifact('viewer', null, { visibility: 'STAGING' }, false)).to.equal(false);
        expect(canReadChatArtifact('viewer', null, { isTemporary: true }, false)).to.equal(false);
        expect(canReadChatArtifact('none', null, visible, false)).to.equal(false);
      });
    });
  });
});
