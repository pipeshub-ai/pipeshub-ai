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

  it('GET / returns notifications for user', async () => {
    const lean = [{ _id: userId, title: 'Hello', status: 'Unread' }];
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

  it('PATCH /:id/read marks notification read', async () => {
    const notifId = new mongoose.Types.ObjectId().toString();
    const doc = { _id: notifId, status: 'Read' };
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
});
