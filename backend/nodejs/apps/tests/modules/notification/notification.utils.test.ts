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
  retentionCutoff,
  DEFAULT_PAGE_SIZE,
  MAX_PAGE_SIZE,
  NOTIFICATION_RETENTION_DAYS,
} from '../../../src/modules/notification/utils/notification.utils';

describe('notification/notification.utils', () => {
  it('retentionCutoff subtracts retention days', () => {
    const now = new Date('2026-05-26T12:00:00.000Z');
    const cutoff = retentionCutoff(now);
    const expected = new Date(now.getTime() - NOTIFICATION_RETENTION_DAYS * 24 * 60 * 60 * 1000);
    expect(cutoff.toISOString()).to.equal(expected.toISOString());
  });

  it('buildRetentionFilter scopes to user and retention window', () => {
    const userOid = new mongoose.Types.ObjectId();
    const filter = buildRetentionFilter(userOid);
    expect(filter.assignedTo).to.equal(userOid);
    expect(filter.isDeleted).to.equal(false);
    const createdAtFilter = filter.createdAt as { $gte: Date };
    expect(createdAtFilter.$gte).to.be.instanceOf(Date);
    expect(createdAtFilter.$gte.getTime()).to.equal(retentionCutoff().getTime());
  });

  it('clampPageSize defaults and caps', () => {
    expect(clampPageSize(undefined)).to.equal(DEFAULT_PAGE_SIZE);
    expect(clampPageSize('abc')).to.equal(DEFAULT_PAGE_SIZE);
    expect(clampPageSize('0')).to.equal(DEFAULT_PAGE_SIZE);
    expect(clampPageSize('25')).to.equal(25);
    expect(clampPageSize(String(MAX_PAGE_SIZE + 10))).to.equal(MAX_PAGE_SIZE);
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
});
