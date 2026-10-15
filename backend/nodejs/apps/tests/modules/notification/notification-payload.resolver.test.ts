/// <reference types="mocha" />
import { expect } from 'chai';
import mongoose from 'mongoose';
import {
  buildNotificationDocForUser,
  getLegacyAssignedToUserIds,
  toBrokerMessage,
} from '../../../src/modules/notification/utils/notification-payload.resolver';

describe('notification/notification-payload.resolver', () => {
  const orgId = new mongoose.Types.ObjectId().toString();

  describe('toBrokerMessage', () => {
    it('rejects null and non-object inputs', () => {
      expect(toBrokerMessage(null)).to.be.null;
      expect(toBrokerMessage(undefined)).to.be.null;
      expect(toBrokerMessage('string')).to.be.null;
      expect(toBrokerMessage(42)).to.be.null;
    });

    it('rejects invalid orgId', () => {
      expect(toBrokerMessage({ orgId: 'bad', type: 'CONNECTOR_ERROR' })).to.be.null;
    });

    it('rejects missing or empty type', () => {
      expect(toBrokerMessage({ orgId, type: '' })).to.be.null;
      expect(toBrokerMessage({ orgId })).to.be.null;
    });

    it('returns the message for a valid input', () => {
      const result = toBrokerMessage({ orgId, type: 'CONNECTOR_ERROR', title: 'T' });
      expect(result).to.not.be.null;
      expect(result!.type).to.equal('CONNECTOR_ERROR');
      expect(result!.orgId).to.equal(orgId);
    });
  });

  describe('buildNotificationDocForUser', () => {
    it('strips broker-only fields and sets defaults', () => {
      const userOid = new mongoose.Types.ObjectId();
      const event = toBrokerMessage({
        orgId,
        type: 'CONNECTOR_ERROR',
        title: 'T',
        recipientUserIds: [userOid.toString()],
        recipientRoles: ['admin'],
      });
      expect(event).to.not.be.null;
      const doc = buildNotificationDocForUser(event!, userOid);
      expect(doc).to.not.have.keys('recipientUserIds', 'recipientRoles');
      expect(String(doc.assignedTo)).to.equal(userOid.toString());
      expect(doc.title).to.equal('T');
      expect(doc.status).to.equal('unread');
      expect(doc.isDeleted).to.equal(false);
    });

    it('preserves an explicit status from the event', () => {
      const userOid = new mongoose.Types.ObjectId();
      const event = toBrokerMessage({ orgId, type: 'CONNECTOR_ERROR', status: 'read' });
      const doc = buildNotificationDocForUser(event!, userOid);
      expect(doc.status).to.equal('read');
    });

    it('converts orgId string to ObjectId on the doc', () => {
      const userOid = new mongoose.Types.ObjectId();
      const event = toBrokerMessage({ orgId, type: 'CONNECTOR_ERROR' });
      const doc = buildNotificationDocForUser(event!, userOid);
      expect(doc.orgId).to.be.instanceOf(mongoose.Types.ObjectId);
      expect((doc.orgId as mongoose.Types.ObjectId).toString()).to.equal(orgId);
    });
  });

  describe('getLegacyAssignedToUserIds', () => {
    it('returns empty array when assignedTo is absent', () => {
      const event = toBrokerMessage({ orgId, type: 'CONNECTOR_ERROR' });
      expect(getLegacyAssignedToUserIds(event!)).to.deep.equal([]);
    });

    it('reads single assignedTo string', () => {
      const legacyUser = new mongoose.Types.ObjectId().toString();
      const event = toBrokerMessage({ orgId, type: 'CONNECTOR_ERROR', assignedTo: legacyUser });
      const ids = getLegacyAssignedToUserIds(event!);
      expect(ids).to.have.length(1);
      expect(ids[0].toString()).to.equal(legacyUser);
    });

    it('reads an array of assignedTo ids and filters invalid entries', () => {
      const user1 = new mongoose.Types.ObjectId().toString();
      const user2 = new mongoose.Types.ObjectId().toString();
      const event = toBrokerMessage({
        orgId,
        type: 'CONNECTOR_ERROR',
        assignedTo: [user1, 'bad-id', user2],
      });
      const ids = getLegacyAssignedToUserIds(event!);
      expect(ids.map((id) => id.toString())).to.have.members([user1, user2]);
    });
  });
  describe('outbox events', () => {
    const user = new mongoose.Types.ObjectId();
    const outboxEvent = {
      orgId,
      type: 'chat.activity',
      recipientUserIds: [user.toString()],
      dedupeKey: 'chat.activity:s1:u1',
      coalesceKey: 'chat.activity:s1',
      emailIntent: { template: 'chatShared', actorName: 'Ada', orgName: 'Acme', accessLevel: 'read' },
      payload: { sessionId: 's1' },
    };

    it('parses a JSON string value into the same message as the object', () => {
      expect(toBrokerMessage(JSON.stringify(outboxEvent))).to.deep.equal(outboxEvent);
    });

    it('rejects a string that is not a JSON object', () => {
      expect(toBrokerMessage('{broken')).to.be.null;
      expect(toBrokerMessage('"text"')).to.be.null;
      expect(toBrokerMessage('[1,2]')).to.be.null;
      expect(toBrokerMessage('null')).to.be.null;
    });

    it('rejects a JSON string with an invalid org or type', () => {
      expect(toBrokerMessage(JSON.stringify({ orgId: 'bad', type: 'x' }))).to.be.null;
      expect(toBrokerMessage(JSON.stringify({ orgId }))).to.be.null;
    });

    it('keeps dedupeKey and coalesceKey on the stored doc but drops emailIntent', () => {
      const event = toBrokerMessage(JSON.stringify(outboxEvent));
      const doc = buildNotificationDocForUser(event!, user);
      expect(doc).to.include({
        dedupeKey: 'chat.activity:s1:u1',
        coalesceKey: 'chat.activity:s1',
      });
      expect(doc).to.not.have.any.keys('emailIntent', 'recipientUserIds', 'recipientRoles');
    });
  });
});
