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

  it('toBrokerMessage rejects invalid orgId', () => {
    expect(toBrokerMessage({ orgId: 'bad', type: 'CONNECTOR_ERROR' })).to.be.null;
  });

  it('buildNotificationDocForUser strips broker-only fields', () => {
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

  it('getLegacyAssignedToUserIds reads single assignedTo', () => {
    const legacyUser = new mongoose.Types.ObjectId().toString();
    const event = toBrokerMessage({
      orgId,
      type: 'CONNECTOR_ERROR',
      assignedTo: legacyUser,
    });
    const ids = getLegacyAssignedToUserIds(event!);
    expect(ids).to.have.length(1);
    expect(ids[0].toString()).to.equal(legacyUser);
  });
});
