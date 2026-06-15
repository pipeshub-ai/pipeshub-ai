/// <reference types="mocha" />
import 'reflect-metadata';
import express from 'express';
import type { Server } from 'http';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import mongoose from 'mongoose';
import { createNotificationRouter } from '../../../../src/modules/notification/routes/notification.routes';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { Notifications } from '../../../../src/modules/notification/schema/notification.schema';
import { encodeCursor } from '../../../../src/modules/notification/utils/notification-api.utils';

describe('notification/routes/notification.routes', () => {
  let container: Container;
  let userId: string;
  let app: express.Express;
  let server: Server | undefined;

  beforeEach(() => {
    userId = new mongoose.Types.ObjectId().toString();
    container = new Container();
    const authMiddleware = {
      authenticate: sinon.stub().callsFake((req: any, _res: any, next: any) => {
        req.user = { userId };
        next();
      }),
    };
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(authMiddleware as any);

    const router = createNotificationRouter(container);
    app = express();
    app.use(express.json());
    app.use('/api/v1/notifications', router);
  });

  afterEach(async () => {
    sinon.restore();
    if (server) {
      await new Promise<void>((resolve, reject) => {
        server!.close((err) => (err ? reject(err) : resolve()));
      });
      server = undefined;
    }
  });

  async function listen(): Promise<number> {
    return new Promise((resolve, reject) => {
      server = app.listen(0, () => {
        const addr = server!.address();
        if (addr && typeof addr === 'object') {
          resolve(addr.port);
        } else {
          reject(new Error('no port'));
        }
      });
    });
  }

  it('GET / returns paginated notifications for user', async () => {
    const lean = [{ _id: userId, title: 'Hello', status: 'unread' }];
    sinon.stub(Notifications, 'find').returns({
      sort: sinon.stub().returnsThis(),
      limit: sinon.stub().returnsThis(),
      lean: sinon.stub().resolves(lean),
    } as any);

    const port = await listen();
    const res = await fetch(`http://127.0.0.1:${port}/api/v1/notifications/`);
    expect(res.status).to.equal(200);
    const body = await res.json();
    expect(body.notifications).to.deep.equal(lean);
    expect(body.hasMore).to.equal(false);
    expect(body.cursor).to.equal(null);
    expect(body.unreadCount).to.equal(undefined);
  });

  it('GET / returns 400 for invalid cursor', async () => {
    const port = await listen();
    const res = await fetch(`http://127.0.0.1:${port}/api/v1/notifications/?cursor=bad`);
    expect(res.status).to.equal(400);
  });

  it('GET / applies cursor filter when cursor provided', async () => {
    const createdAt = new Date('2026-05-20T10:30:00.000Z');
    const id = new mongoose.Types.ObjectId();
    const cursor = encodeCursor({ createdAt, _id: id });
    const findStub = sinon.stub(Notifications, 'find').returns({
      sort: sinon.stub().returnsThis(),
      limit: sinon.stub().returnsThis(),
      lean: sinon.stub().resolves([]),
    } as any);

    const port = await listen();
    const res = await fetch(
      `http://127.0.0.1:${port}/api/v1/notifications/?cursor=${encodeURIComponent(cursor)}`,
    );
    expect(res.status).to.equal(200);

    const filterArg = findStub.firstCall.args[0] as Record<string, unknown>;
    expect(filterArg.$or).to.deep.equal([
      { createdAt: { $lt: createdAt } },
      { createdAt, _id: { $lt: id } },
    ]);
  });

  it('GET / returns 401 when userId missing', async () => {
    container.unbind('AuthMiddleware');
    container
      .bind<AuthMiddleware>('AuthMiddleware')
      .toConstantValue({
        authenticate: sinon.stub().callsFake((req: any, _res: any, next: any) => {
          req.user = {};
          next();
        }),
      } as any);
    const router = createNotificationRouter(container);
    const app401 = express();
    app401.use('/api/v1/notifications', router);
    const srv = app401.listen(0);
    const port = await new Promise<number>((resolve, reject) => {
      srv.on('listening', () => {
        const a = srv.address();
        if (a && typeof a === 'object') resolve(a.port);
        else reject(new Error('no port'));
      });
    });
    const res = await fetch(`http://127.0.0.1:${port}/api/v1/notifications/`);
    expect(res.status).to.equal(401);
    await new Promise<void>((resolve, reject) => srv.close((err) => (err ? reject(err) : resolve())));
  });

  it('PATCH /read-all marks all unread notifications read', async () => {
    const updateManyStub = sinon.stub(Notifications, 'updateMany').resolves({
      acknowledged: true,
      modifiedCount: 3,
      matchedCount: 3,
      upsertedCount: 0,
      upsertedId: null,
    });

    const port = await listen();
    const res = await fetch(`http://127.0.0.1:${port}/api/v1/notifications/read-all`, {
      method: 'PATCH',
    });
    expect(res.status).to.equal(200);
    const body = await res.json();
    expect(body.success).to.equal(true);
    expect(body.modifiedCount).to.equal(3);

    const filterArg = updateManyStub.firstCall.args[0] as Record<string, unknown>;
    expect(filterArg.status).to.equal('unread');
    expect(String(filterArg.assignedTo)).to.equal(userId);
    expect(filterArg.isDeleted).to.equal(false);
  });

  it('PATCH /read-all returns 401 when userId missing', async () => {
    container.unbind('AuthMiddleware');
    container
      .bind<AuthMiddleware>('AuthMiddleware')
      .toConstantValue({
        authenticate: sinon.stub().callsFake((req: any, _res: any, next: any) => {
          req.user = {};
          next();
        }),
      } as any);
    const router = createNotificationRouter(container);
    const app401 = express();
    app401.use('/api/v1/notifications', router);
    const srv = app401.listen(0);
    const port = await new Promise<number>((resolve, reject) => {
      srv.on('listening', () => {
        const a = srv.address();
        if (a && typeof a === 'object') resolve(a.port);
        else reject(new Error('no port'));
      });
    });
    const res = await fetch(`http://127.0.0.1:${port}/api/v1/notifications/read-all`, {
      method: 'PATCH',
    });
    expect(res.status).to.equal(401);
    await new Promise<void>((resolve, reject) =>
      srv.close((err) => (err ? reject(err) : resolve())),
    );
  });

  it('PATCH /:id/read marks notification read', async () => {
    const notifId = new mongoose.Types.ObjectId().toString();
    const doc = { _id: notifId, status: 'read' };
    sinon.stub(Notifications, 'findOneAndUpdate').returns({
      lean: sinon.stub().resolves(doc),
    } as any);

    const port = await listen();
    const res = await fetch(
      `http://127.0.0.1:${port}/api/v1/notifications/${notifId}/read`,
      { method: 'PATCH' },
    );
    expect(res.status).to.equal(200);
    const body = await res.json();
    expect(body.notification).to.deep.equal(doc);
  });

  it('PATCH /:id/unread marks notification unread', async () => {
    const notifId = new mongoose.Types.ObjectId().toString();
    const doc = { _id: notifId, status: 'unread' };
    const stub = sinon.stub(Notifications, 'findOneAndUpdate').returns({
      lean: sinon.stub().resolves(doc),
    } as any);

    const port = await listen();
    const res = await fetch(
      `http://127.0.0.1:${port}/api/v1/notifications/${notifId}/unread`,
      { method: 'PATCH' },
    );
    expect(res.status).to.equal(200);
    const body = await res.json();
    expect(body.notification).to.deep.equal(doc);

    const update = stub.firstCall.args[1] as Record<string, unknown>;
    expect((update as any).$set.status).to.equal('unread');
  });

  it('PATCH /:id/unread returns 404 when notification not found', async () => {
    const notifId = new mongoose.Types.ObjectId().toString();
    sinon.stub(Notifications, 'findOneAndUpdate').returns({
      lean: sinon.stub().resolves(null),
    } as any);

    const port = await listen();
    const res = await fetch(
      `http://127.0.0.1:${port}/api/v1/notifications/${notifId}/unread`,
      { method: 'PATCH' },
    );
    expect(res.status).to.equal(404);
  });

  it('PATCH /:id/unread returns 400 for invalid id', async () => {
    const port = await listen();
    const res = await fetch(
      `http://127.0.0.1:${port}/api/v1/notifications/not-an-objectid/unread`,
      { method: 'PATCH' },
    );
    expect(res.status).to.equal(400);
  });

  it('DELETE /:id soft-deletes notification', async () => {
    const notifId = new mongoose.Types.ObjectId().toString();
    sinon.stub(Notifications, 'findOneAndUpdate').returns({
      lean: sinon.stub().resolves({ _id: notifId }),
    } as any);

    const port = await listen();
    const res = await fetch(`http://127.0.0.1:${port}/api/v1/notifications/${notifId}`, {
      method: 'DELETE',
    });
    expect(res.status).to.equal(200);
    const body = await res.json();
    expect(body.success).to.equal(true);
  });

  it('PATCH /:id/archive archives notification', async () => {
    const notifId = new mongoose.Types.ObjectId().toString();
    const doc = { _id: notifId, status: 'archived' };
    const stub = sinon.stub(Notifications, 'findOneAndUpdate').returns({
      lean: sinon.stub().resolves(doc),
    } as any);

    const port = await listen();
    const res = await fetch(
      `http://127.0.0.1:${port}/api/v1/notifications/${notifId}/archive`,
      { method: 'PATCH' },
    );
    expect(res.status).to.equal(200);
    const body = await res.json();
    expect(body.notification).to.deep.equal(doc);

    const update = stub.firstCall.args[1] as Record<string, unknown>;
    expect((update as any).$set.status).to.equal('archived');
  });

  it('PATCH /:id/archive returns 404 when notification not found', async () => {
    const notifId = new mongoose.Types.ObjectId().toString();
    sinon.stub(Notifications, 'findOneAndUpdate').returns({
      lean: sinon.stub().resolves(null),
    } as any);

    const port = await listen();
    const res = await fetch(
      `http://127.0.0.1:${port}/api/v1/notifications/${notifId}/archive`,
      { method: 'PATCH' },
    );
    expect(res.status).to.equal(404);
  });

  it('PATCH /:id/archive returns 400 for invalid id', async () => {
    const port = await listen();
    const res = await fetch(
      `http://127.0.0.1:${port}/api/v1/notifications/not-an-objectid/archive`,
      { method: 'PATCH' },
    );
    expect(res.status).to.equal(400);
  });

  it('PATCH /:id/unarchive unarchives notification and sets status to read', async () => {
    const notifId = new mongoose.Types.ObjectId().toString();
    const doc = { _id: notifId, status: 'read' };
    const stub = sinon.stub(Notifications, 'findOneAndUpdate').returns({
      lean: sinon.stub().resolves(doc),
    } as any);

    const port = await listen();
    const res = await fetch(
      `http://127.0.0.1:${port}/api/v1/notifications/${notifId}/unarchive`,
      { method: 'PATCH' },
    );
    expect(res.status).to.equal(200);
    const body = await res.json();
    expect(body.notification).to.deep.equal(doc);

    const filter = stub.firstCall.args[0] as Record<string, unknown>;
    expect(filter.status).to.equal('archived');
    const update = stub.firstCall.args[1] as Record<string, unknown>;
    expect((update as any).$set.status).to.equal('read');
  });

  it('PATCH /:id/unarchive returns 404 when notification not found', async () => {
    const notifId = new mongoose.Types.ObjectId().toString();
    sinon.stub(Notifications, 'findOneAndUpdate').returns({
      lean: sinon.stub().resolves(null),
    } as any);

    const port = await listen();
    const res = await fetch(
      `http://127.0.0.1:${port}/api/v1/notifications/${notifId}/unarchive`,
      { method: 'PATCH' },
    );
    expect(res.status).to.equal(404);
  });

  it('PATCH /:id/unarchive returns 400 for invalid id', async () => {
    const port = await listen();
    const res = await fetch(
      `http://127.0.0.1:${port}/api/v1/notifications/not-an-objectid/unarchive`,
      { method: 'PATCH' },
    );
    expect(res.status).to.equal(400);
  });

  it('GET /stats returns unreadCount, readCount, archivedCount', async () => {
    const countStub = sinon.stub(Notifications, 'countDocuments');
    countStub.onFirstCall().resolves(5);   // unread
    countStub.onSecondCall().resolves(12); // read
    countStub.onThirdCall().resolves(3);   // archived

    const port = await listen();
    const res = await fetch(`http://127.0.0.1:${port}/api/v1/notifications/stats`);
    expect(res.status).to.equal(200);
    const body = await res.json();
    expect(body.unreadCount).to.equal(5);
    expect(body.readCount).to.equal(12);
    expect(body.archivedCount).to.equal(3);
  });

  it('GET /stats returns 401 when userId missing', async () => {
    container.unbind('AuthMiddleware');
    container
      .bind<AuthMiddleware>('AuthMiddleware')
      .toConstantValue({
        authenticate: sinon.stub().callsFake((req: any, _res: any, next: any) => {
          req.user = {};
          next();
        }),
      } as any);
    const router = createNotificationRouter(container);
    const app401 = express();
    app401.use('/api/v1/notifications', router);
    const srv = app401.listen(0);
    const port = await new Promise<number>((resolve, reject) => {
      srv.on('listening', () => {
        const a = srv.address();
        if (a && typeof a === 'object') resolve(a.port);
        else reject(new Error('no port'));
      });
    });
    const res = await fetch(`http://127.0.0.1:${port}/api/v1/notifications/stats`);
    expect(res.status).to.equal(401);
    await new Promise<void>((resolve, reject) =>
      srv.close((err) => (err ? reject(err) : resolve())),
    );
  });
});
