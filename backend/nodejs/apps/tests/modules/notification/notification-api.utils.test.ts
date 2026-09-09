/// <reference types="mocha" />
import { expect } from 'chai';
import mongoose from 'mongoose';
import {
  buildCursorFilter,
  buildRetentionFilter,
  clampPageSize,
  decodeCursor,
  encodeCursor,
  InvalidNotificationCursorError,
  paginateResults,
  resolveNotificationAuthContext,
  retentionCutoff,
  DEFAULT_PAGE_SIZE,
  MAX_PAGE_SIZE,
  NOTIFICATION_RETENTION_DAYS,
} from '../../../src/modules/notification/utils/notification-api.utils';

describe('notification/notification-api.utils', () => {
  it('retentionCutoff subtracts retention days', () => {
    const now = new Date('2026-05-26T12:00:00.000Z');
    const cutoff = retentionCutoff(now);
    const expected = new Date(now.getTime() - NOTIFICATION_RETENTION_DAYS * 24 * 60 * 60 * 1000);
    expect(cutoff.toISOString()).to.equal(expected.toISOString());
  });

  it('buildRetentionFilter scopes to org, user and retention window', () => {
    const orgOid = new mongoose.Types.ObjectId();
    const userOid = new mongoose.Types.ObjectId();
    const before = retentionCutoff().getTime();
    const filter = buildRetentionFilter(orgOid, userOid, null);
    const after = retentionCutoff().getTime();
    expect(filter.orgId).to.equal(orgOid);
    expect(filter.assignedTo).to.equal(userOid);
    expect(filter.isDeleted).to.equal(false);
    const createdAtFilter = filter.createdAt as { $gte: Date };
    expect(createdAtFilter.$gte).to.be.instanceOf(Date);
    const cutoffMs = createdAtFilter.$gte.getTime();
    expect(cutoffMs).to.be.at.least(before);
    expect(cutoffMs).to.be.at.most(after);
    // Default (no status, includeArchived=false) excludes archived docs.
    expect(filter.status).to.deep.equal({ $ne: 'archived' });
  });

  it('buildRetentionFilter includes all statuses when includeArchived=true', () => {
    const orgOid = new mongoose.Types.ObjectId();
    const userOid = new mongoose.Types.ObjectId();
    const filter = buildRetentionFilter(orgOid, userOid, null, true);
    expect(filter.status).to.be.undefined;
  });

  it('buildRetentionFilter includes status when provided', () => {
    const orgOid = new mongoose.Types.ObjectId();
    const userOid = new mongoose.Types.ObjectId();
    const filter = buildRetentionFilter(orgOid, userOid, 'unread');
    expect(filter.status).to.equal('unread');
  });

  it('resolveNotificationAuthContext returns ObjectIds for a valid user', () => {
    const userId = new mongoose.Types.ObjectId().toString();
    const orgId = new mongoose.Types.ObjectId().toString();
    const context = resolveNotificationAuthContext({ userId, orgId });
    expect(context).to.not.be.null;
    expect(context!.userOid.toString()).to.equal(userId);
    expect(context!.orgOid.toString()).to.equal(orgId);
  });

  it('resolveNotificationAuthContext returns null when userId is missing or invalid', () => {
    const orgId = new mongoose.Types.ObjectId().toString();
    expect(resolveNotificationAuthContext(undefined)).to.be.null;
    expect(resolveNotificationAuthContext({ orgId })).to.be.null;
    expect(resolveNotificationAuthContext({ userId: 'not-an-id', orgId })).to.be.null;
  });

  it('resolveNotificationAuthContext returns null when orgId is missing or invalid', () => {
    const userId = new mongoose.Types.ObjectId().toString();
    expect(resolveNotificationAuthContext({ userId })).to.be.null;
    expect(resolveNotificationAuthContext({ userId, orgId: 'not-an-id' })).to.be.null;
  });

  it('clampPageSize defaults and caps', () => {
    expect(clampPageSize(undefined)).to.equal(DEFAULT_PAGE_SIZE);
    expect(clampPageSize('abc')).to.equal(DEFAULT_PAGE_SIZE);
    expect(clampPageSize('0')).to.equal(DEFAULT_PAGE_SIZE);
    expect(clampPageSize('25')).to.equal(25);
    expect(clampPageSize(String(MAX_PAGE_SIZE + 10))).to.equal(MAX_PAGE_SIZE);
  });

  it('clampPageSize handles negative and float inputs', () => {
    expect(clampPageSize(-5)).to.equal(DEFAULT_PAGE_SIZE);
    expect(clampPageSize('3.9')).to.equal(3);
    expect(clampPageSize(1)).to.equal(1);
    expect(clampPageSize(null)).to.equal(DEFAULT_PAGE_SIZE);
  });

  it('encodeCursor and decodeCursor round-trip', () => {
    const createdAt = new Date('2026-05-20T10:30:00.000Z');
    const id = new mongoose.Types.ObjectId();
    const encoded = encodeCursor({ createdAt, _id: id });
    const decoded = decodeCursor(encoded);
    expect(decoded.createdAt.toISOString()).to.equal(createdAt.toISOString());
    expect(decoded._id.toString()).to.equal(id.toString());
  });

  it('decodeCursor rejects invalid values', () => {
    expect(() => decodeCursor('')).to.throw(InvalidNotificationCursorError);
    expect(() => decodeCursor('not-base64')).to.throw(InvalidNotificationCursorError);
    const badId = Buffer.from(JSON.stringify({ c: new Date().toISOString(), i: 'bad' })).toString(
      'base64url',
    );
    expect(() => decodeCursor(badId)).to.throw(InvalidNotificationCursorError);
  });

  it('decodeCursor rejects missing c field', () => {
    const missingC = Buffer.from(
      JSON.stringify({ i: new mongoose.Types.ObjectId().toString() }),
    ).toString('base64url');
    expect(() => decodeCursor(missingC)).to.throw(InvalidNotificationCursorError);
  });

  it('decodeCursor rejects invalid date in c field', () => {
    const badDate = Buffer.from(
      JSON.stringify({ c: 'not-a-date', i: new mongoose.Types.ObjectId().toString() }),
    ).toString('base64url');
    expect(() => decodeCursor(badDate)).to.throw(InvalidNotificationCursorError);
  });

  it('buildCursorFilter uses $or tie-break', () => {
    const createdAt = new Date('2026-05-20T10:30:00.000Z');
    const id = new mongoose.Types.ObjectId();
    const filter = buildCursorFilter({ createdAt, _id: id });
    expect(filter).to.deep.equal({
      $or: [
        { createdAt: { $lt: createdAt } },
        { createdAt, _id: { $lt: id } },
      ],
    });
  });

  it('paginateResults slices and sets hasMore', () => {
    const rows = Array.from({ length: 3 }, (_, i) => ({
      _id: new mongoose.Types.ObjectId(),
      createdAt: new Date(Date.UTC(2026, 4, 20, 10, 30 - i)),
    }));
    const page = paginateResults(rows, 2);
    expect(page.notifications).to.have.lengthOf(2);
    expect(page.hasMore).to.be.true;
    expect(page.cursor).to.be.a('string');
  });

  it('paginateResults returns null cursor when no more', () => {
    const row = {
      _id: new mongoose.Types.ObjectId(),
      createdAt: new Date(),
    };
    const page = paginateResults([row], 20);
    expect(page.hasMore).to.be.false;
    expect(page.cursor).to.be.null;
  });

  it('paginateResults returns null cursor for empty array', () => {
    const page = paginateResults([], 20);
    expect(page.hasMore).to.be.false;
    expect(page.notifications).to.have.lengthOf(0);
    expect(page.cursor).to.be.null;
  });

  it('paginateResults returns null cursor when last item lacks valid _id or createdAt', () => {
    const rows = [{ foo: 'bar' }, { foo: 'baz' }];
    const page = paginateResults(rows, 1);
    expect(page.hasMore).to.be.true;
    expect(page.cursor).to.be.null;
  });
});
