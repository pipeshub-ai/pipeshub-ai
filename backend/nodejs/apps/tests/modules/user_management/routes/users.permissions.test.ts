import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import mongoose from 'mongoose';
import jwt from 'jsonwebtoken';
import { deriveUserActionSecret } from '../../../../src/libs/utils/jwtKeys';
import { Container } from 'inversify';
import { Logger } from '../../../../src/libs/services/logger.service';
import { createUserRouter } from '../../../../src/modules/user_management/routes/users.routes';
import { UserController } from '../../../../src/modules/user_management/controller/users.controller';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import {
  authJwtGenerator,
  fetchConfigJwtGenerator,
  iamUserLookupJwtGenerator,
} from '../../../../src/libs/utils/createJwt';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { UserCredentials } from '../../../../src/modules/auth/schema/userCredentials.schema';
import { UserActivities } from '../../../../src/modules/auth/schema/userActivities.schema';
import { UserDisplayPicture } from '../../../../src/modules/user_management/schema/userDp.schema';
import { userActivitiesType } from '../../../../src/libs/utils/userActivities.utils';
import * as appConfigModule from '../../../../src/modules/tokens_manager/config/config';

// Serves the real user router over HTTP with its real middleware chain
// (AuthMiddleware, userAdminCheck, userAdminOrSelfCheck, userExists) and the
// real UserController. Tokens are real JWTs. Only Mongo and the event bus are
// faked: the Users collection is an in-memory table the fake queries filter.

const JWT_SECRET = 'users-permissions-jwt-secret';
const SCOPED_SECRET = 'users-permissions-scoped-secret';

type Row = Record<string, unknown>;

interface FakeQuery<T> {
  select(): FakeQuery<T>;
  lean(): FakeQuery<T>;
  sort(): FakeQuery<T>;
  exec(): Promise<T>;
  then<R>(ok: (v: T) => R, fail?: (e: unknown) => R): Promise<R>;
}

function matches(row: Row, filter: Row): boolean {
  return Object.entries(filter).every(([key, cond]) => {
    const value = row[key];
    if (cond && typeof cond === 'object' && !(cond instanceof mongoose.Types.ObjectId)) {
      const op = cond as { $ne?: unknown; $in?: unknown[] };
      if ('$ne' in op) return String(value) !== String(op.$ne);
      if (Array.isArray(op.$in)) return op.$in.some((c) => String(c) === String(value));
      return false;
    }
    if (cond === false && value === undefined) return true;
    return String(value) === String(cond);
  });
}

class FakeTable {
  rows: Row[] = [];

  insert(row: Row) {
    this.rows.push({ ...row });
    return row;
  }

  get(id: string) {
    return this.rows.find((r) => String(r._id) === String(id));
  }

  doc(row: Row) {
    const copy: Row = { ...row };
    Object.defineProperty(copy, 'save', {
      enumerable: false,
      value: async () => {
        Object.assign(row, copy);
        return copy;
      },
    });
    Object.defineProperty(copy, 'toObject', {
      enumerable: false,
      value: () => ({ ...copy }),
    });
    return copy;
  }

  query<T>(compute: () => T): FakeQuery<T> {
    const q: FakeQuery<T> = {
      select: () => q,
      lean: () => q,
      sort: () => q,
      exec: async () => compute(),
      then: (ok, fail) => Promise.resolve().then(compute).then(ok, fail),
    };
    return q;
  }

  findOne(filter: Row) {
    return this.query(() => {
      const row = this.rows.find((r) => matches(r, filter));
      return row ? this.doc(row) : null;
    });
  }

  find(filter: Row) {
    return this.query(() => this.rows.filter((r) => matches(r, filter)).map((r) => ({ ...r })));
  }
}

const orgA = new mongoose.Types.ObjectId().toHexString();
const orgB = new mongoose.Types.ObjectId().toHexString();
const oid = () => new mongoose.Types.ObjectId().toHexString();

describe('User routes: who may do what', () => {
  let users: FakeTable;
  let credentials: FakeTable;
  let server: Server;
  let baseUrl: string;
  let events: { start: sinon.SinonStub; stop: sinon.SinonStub; publishEvent: sinon.SinonStub };
  let mail: { sendMail: sinon.SinonStub };
  let ids: Record<
    'adminA' | 'memberA' | 'otherMemberA' | 'secondAdminA' | 'adminB' | 'memberB' | 'deletedA' | 'disabledA',
    string
  >;

  function person(name: string, orgId: string, role: 'admin' | 'member', extra: Row = {}) {
    const id = oid();
    users.insert({
      _id: id,
      orgId,
      email: `${name}@${orgId === orgA ? 'a' : 'b'}.test`,
      fullName: name,
      role,
      isDeleted: false,
      ...extra,
    });
    return id;
  }

  function sessionFor(userId: string, opts: { role?: 'admin' | 'member' | null } = {}) {
    const row = users.get(userId)!;
    const role = (opts.role === undefined ? row.role : opts.role) as 'admin' | 'member' | null;
    return authJwtGenerator(
      JWT_SECRET,
      String(row.email),
      userId,
      String(row.orgId),
      String(row.fullName),
      'business',
      role,
    );
  }

  async function call(
    method: string,
    path: string,
    token: string | null,
    body?: unknown,
  ): Promise<{ status: number; body: unknown }> {
    const response = await fetch(`${baseUrl}/users${path}`, {
      method,
      headers: {
        'content-type': 'application/json',
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    let parsed: unknown = text;
    try {
      parsed = JSON.parse(text);
    } catch {
      // plain-text answers stay as text
    }
    return { status: response.status, body: parsed };
  }

  function errorMessage(res: { body: unknown }): string {
    const body = res.body as { error?: { message?: string }; message?: string } | null;
    return body?.error?.message ?? body?.message ?? '';
  }

  beforeEach(async () => {
    users = new FakeTable();
    credentials = new FakeTable();
    ids = {
      adminA: person('ada', orgA, 'admin'),
      memberA: person('max', orgA, 'member'),
      otherMemberA: person('mia', orgA, 'member'),
      secondAdminA: person('abe', orgA, 'admin'),
      adminB: person('bea', orgB, 'admin'),
      memberB: person('bob', orgB, 'member'),
      deletedA: person('dan', orgA, 'member', { isDeleted: true }),
      disabledA: person('dee', orgA, 'member', { isDisabled: true }),
    };

    sinon.stub(Users, 'findOne').callsFake(((f: Row) => users.findOne(f)) as unknown as typeof Users.findOne);
    sinon.stub(Users, 'find').callsFake(((f: Row) => users.find(f)) as unknown as typeof Users.find);
    sinon.stub(Users, 'countDocuments').callsFake(((f: Row) =>
      users.query(() => users.rows.filter((r) => matches(r, f)).length)) as unknown as typeof Users.countDocuments);
    sinon.stub(Users, 'updateOne').callsFake(((f: Row, u: { $set?: Row }) =>
      users.query(() => {
        const row = users.rows.find((r) => matches(r, f));
        if (row) Object.assign(row, u.$set ?? {});
        return { modifiedCount: row ? 1 : 0 };
      })) as unknown as typeof Users.updateOne);
    sinon.stub(Org, 'findOne').callsFake(((f: Row) =>
      users.query(() =>
        [orgA, orgB].includes(String(f._id)) ? { _id: f._id, accountType: 'business', isDeleted: false } : null,
      )) as unknown as typeof Org.findOne);
    sinon.stub(UserActivities, 'findOne').callsFake((() => users.query(() => null)) as unknown as typeof UserActivities.findOne);
    sinon.stub(UserActivities, 'insertMany').resolves([]);
    sinon.stub(UserDisplayPicture, 'find').callsFake((() => users.query(() => [])) as unknown as typeof UserDisplayPicture.find);
    sinon.stub(UserCredentials, 'findOneAndUpdate').callsFake(((f: Row, u: { $set?: Row }) => {
      const row = credentials.rows.find((r) => matches(r, f));
      if (row) Object.assign(row, u.$set ?? {});
      return Promise.resolve(row ?? null);
    }) as unknown as typeof UserCredentials.findOneAndUpdate);

    events = {
      start: sinon.stub().resolves(),
      stop: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
    };
    const logger = {
      debug: sinon.stub(),
      info: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
    } as unknown as Logger;
    const config: Partial<appConfigModule.AppConfig> = {
      jwtSecret: JWT_SECRET,
      scopedJwtSecret: SCOPED_SECRET,
      cmBackend: 'http://cm',
      frontendUrl: 'http://app',
      rsAvailable: 'false',
    };

    const container = new Container();
    container.bind('Logger').toConstantValue(logger);
    container.bind('AppConfig').toConstantValue(config);
    container
      .bind('AuthMiddleware')
      .toConstantValue(new AuthMiddleware(logger, new AuthTokenService(JWT_SECRET, SCOPED_SECRET)));
    mail = { sendMail: sinon.stub().resolves({ statusCode: 200 }) };
    container.bind('MailService').toConstantValue(mail);
    container.bind('AuthService').toConstantValue({});
    container.bind('EntitiesEventProducer').toConstantValue(events);
    container.bind('NotificationProducer').toConstantValue({});
    container.bind('OrgController').toConstantValue({});
    container
      .bind('UserController')
      .toConstantValue(
        new UserController(
          ...([config, mail, {}, logger, events, {}] as unknown as ConstructorParameters<
            typeof UserController
          >),
        ),
      );

    const app = express();
    app.use(express.json());
    app.use('/users', createUserRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = await new Promise<Server>((resolve) => {
      const s = app.listen(0, '127.0.0.1', () => resolve(s));
    });
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}/`.replace(/\/$/, '');
  });

  afterEach(async () => {
    sinon.restore();
    await new Promise((resolve) => server.close(resolve));
  });

  describe('changing a user', () => {
    it("stops a member from editing another member's profile", async () => {
      const res = await call('PUT', `/${ids.otherMemberA}`, sessionFor(ids.memberA), {
        designation: 'Owned',
      });

      expect(res.status).to.equal(400);
      expect(errorMessage(res)).to.match(/admin access/);
      expect(users.get(ids.otherMemberA)!.designation).to.be.undefined;
    });

    it('stops a member from making themselves an admin', async () => {
      const res = await call('PUT', `/${ids.memberA}`, sessionFor(ids.memberA), { role: 'admin' });

      expect(res.status).to.equal(403);
      expect(errorMessage(res)).to.equal('Only admins can change user roles');
      expect(users.get(ids.memberA)!.role).to.equal('member');
    });

    it('refuses to move a user to another org through the request body', async () => {
      const res = await call('PUT', `/${ids.memberA}`, sessionFor(ids.memberA), {
        orgId: orgB,
        designation: 'x',
      });

      expect(res.status).to.equal(400);
      expect(errorMessage(res)).to.match(/aren't accepted here: orgId/);
      expect(users.get(ids.memberA)!.orgId).to.equal(orgA);
    });

    it('lets a member update their own profile', async () => {
      const res = await call('PUT', `/${ids.memberA}`, sessionFor(ids.memberA), {
        designation: 'Engineer',
      });

      expect(res.status).to.equal(200);
      expect(res.body).to.have.property('designation', 'Engineer');
      expect(users.get(ids.memberA)!.designation).to.equal('Engineer');
      expect(events.publishEvent.calledOnce).to.be.true;
    });

    it('does not switch the email straight away; it mails a confirmation link to the new address', async () => {
      const res = await call('PUT', `/${ids.memberA}`, sessionFor(ids.memberA), {
        email: 'max.new@a.test',
      });

      expect(res.status).to.equal(200);
      expect(res.body).to.have.deep.property('meta', { emailChangeMailStatus: 'sent' });
      expect(users.get(ids.memberA)!.email).to.equal('max@a.test');
      const sent = mail.sendMail.firstCall.args[0] as {
        usersMails: string[];
        templateData: { link: string };
      };
      expect(sent.usersMails).to.deep.equal(['max.new@a.test']);
      const link: string = sent.templateData.link;
      expect(link.startsWith('http://app/reset-email#token=')).to.be.true;
      const claims = jwt.verify(
        link.split('#token=')[1] ?? '',
        deriveUserActionSecret(SCOPED_SECRET),
      ) as { userId?: string; newEmail?: string };
      expect(claims).to.include({ userId: ids.memberA, newEmail: 'max.new@a.test' });
    });

    it('reports a failed confirmation email and still leaves the old address in place', async () => {
      mail.sendMail.resolves({ statusCode: 500 });

      const res = await call('PUT', `/${ids.memberA}`, sessionFor(ids.memberA), {
        email: 'max.new@a.test',
      });

      expect(res.status).to.equal(200);
      expect(res.body).to.have.deep.property('meta', { emailChangeMailStatus: 'failed' });
      expect(users.get(ids.memberA)!.email).to.equal('max@a.test');
    });

    it("refuses an email another user in the org already has", async () => {
      const res = await call('PUT', `/${ids.memberA}`, sessionFor(ids.memberA), {
        email: 'mia@a.test',
      });

      expect(res.status).to.equal(400);
      expect(errorMessage(res)).to.equal('Email already exists for another user');
      expect(mail.sendMail.called).to.be.false;
    });

    it("stops an admin from editing a user in someone else's org", async () => {
      const res = await call('PUT', `/${ids.memberB}`, sessionFor(ids.adminA), {
        designation: 'Owned',
      });

      expect(res.status).to.equal(404);
      expect(users.get(ids.memberB)!.designation).to.be.undefined;
    });

    it("stops a member from renaming another member", async () => {
      const res = await call('PATCH', `/${ids.otherMemberA}/fullname`, sessionFor(ids.memberA), {
        fullName: 'Renamed',
      });

      expect(res.status).to.equal(400);
      expect(users.get(ids.otherMemberA)!.fullName).to.equal('mia');
    });

    it('lets an admin promote a member, and signs the member out everywhere', async () => {
      const res = await call('PUT', `/${ids.memberA}`, sessionFor(ids.adminA), { role: 'admin' });

      expect(res.status).to.equal(200);
      expect(users.get(ids.memberA)!.role).to.equal('admin');
      const activities = (UserActivities.insertMany as unknown as sinon.SinonStub).firstCall
        .args[0] as Row[];
      expect(activities).to.have.length(1);
      expect(activities[0]).to.include({ activityType: userActivitiesType.ROLE_CHANGED });
      expect(String(activities[0]?.userId)).to.equal(ids.memberA);
    });

    it('refuses a role that is neither admin nor member', async () => {
      const res = await call('PUT', `/${ids.memberA}`, sessionFor(ids.adminA), { role: 'owner' });

      expect(res.status).to.equal(400);
      expect(errorMessage(res)).to.equal('Role must be one of: admin, member.');
      expect(users.get(ids.memberA)!.role).to.equal('member');
    });

    it('keeps the last admin an admin', async () => {
      users.get(ids.secondAdminA)!.role = 'member';

      const res = await call('PUT', `/${ids.adminA}`, sessionFor(ids.adminA), { role: 'member' });

      expect(res.status).to.equal(400);
      expect(users.get(ids.adminA)!.role).to.equal('admin');
    });

    it('lets an admin step down while another admin remains', async () => {
      const res = await call('PUT', `/${ids.adminA}`, sessionFor(ids.adminA), { role: 'member' });

      expect(res.status).to.equal(200);
      expect(users.get(ids.adminA)!.role).to.equal('member');
    });
  });

  describe('reading users', () => {
    it("stops a member from reading another user's email", async () => {
      const res = await call('GET', `/${ids.otherMemberA}/email`, sessionFor(ids.memberA));

      expect(res.status).to.equal(400);
      expect(JSON.stringify(res.body)).to.not.include('mia@');
    });

    it("lets an admin read a user's email in their own org", async () => {
      const res = await call('GET', `/${ids.memberA}/email`, sessionFor(ids.adminA));

      expect(res.status).to.equal(200);
      expect(res.body).to.deep.equal({ email: 'max@a.test' });
    });

    it('does not show a user from another org, even to an admin', async () => {
      const res = await call('GET', `/${ids.memberB}`, sessionFor(ids.adminA));

      expect(res.status).to.equal(404);
    });

    it('returns only same-org users when asked for a mix of ids', async () => {
      const res = await call('POST', '/by-ids', sessionFor(ids.memberA), {
        userIds: [ids.otherMemberA, ids.memberB, ids.adminB],
      });

      expect(res.status).to.equal(200);
      expect((res.body as Row[]).map((u) => u._id)).to.deep.equal([ids.otherMemberA]);
    });
  });

  describe('removing and unblocking users', () => {
    it('stops a member from deleting anyone', async () => {
      const res = await call('DELETE', `/${ids.otherMemberA}`, sessionFor(ids.memberA));

      expect(res.status).to.equal(400);
      expect(errorMessage(res)).to.equal('Admin access required');
      expect(users.get(ids.otherMemberA)!.isDeleted).to.be.false;
    });

    it('asks an admin to demote another admin before deleting them', async () => {
      const res = await call('DELETE', `/${ids.secondAdminA}`, sessionFor(ids.adminA));

      expect(res.status).to.equal(400);
      expect(errorMessage(res)).to.match(/demote the user from admin first/);
      expect(users.get(ids.secondAdminA)!.isDeleted).to.be.false;
    });

    it("stops an admin from deleting a user in someone else's org", async () => {
      const res = await call('DELETE', `/${ids.memberB}`, sessionFor(ids.adminA));

      expect(res.status).to.equal(404);
      expect(users.get(ids.memberB)!.isDeleted).to.be.false;
    });

    it('stops a member from unblocking an account', async () => {
      credentials.insert({ userId: ids.otherMemberA, orgId: orgA, isBlocked: true, isDeleted: false });

      const res = await call('PUT', `/${ids.otherMemberA}/unblock`, sessionFor(ids.memberA));

      expect(res.status).to.equal(400);
      expect(credentials.rows[0]?.isBlocked).to.be.true;
    });

    it("stops an admin from unblocking an account in someone else's org", async () => {
      credentials.insert({ userId: ids.memberB, orgId: orgB, isBlocked: true, isDeleted: false });

      const res = await call('PUT', `/${ids.memberB}/unblock`, sessionFor(ids.adminA));

      expect(res.status).to.equal(400);
      expect(errorMessage(res)).to.equal('User not found or not blocked');
      expect(credentials.rows[0]?.isBlocked).to.be.true;
    });

    it('lets an admin unblock an account in their own org', async () => {
      credentials.insert({
        userId: ids.memberA,
        orgId: orgA,
        isBlocked: true,
        isDeleted: false,
        wrongCredentialCount: 5,
      });

      const res = await call('PUT', `/${ids.memberA}/unblock`, sessionFor(ids.adminA));

      expect(res.status).to.equal(200);
      expect(credentials.rows[0]).to.include({ isBlocked: false, wrongCredentialCount: 0 });
    });
  });

  describe('sessions for accounts that should no longer have one', () => {
    it('turns away a deleted user whose session token has not expired', async () => {
      const res = await call('GET', `/${ids.memberA}`, sessionFor(ids.deletedA));

      expect(res.status).to.equal(401);
    });

    it('turns away a disabled user whose session token has not expired', async () => {
      const res = await call('GET', `/${ids.memberA}`, sessionFor(ids.disabledA));

      expect(res.status).to.equal(401);
      expect(errorMessage(res)).to.equal('This account is disabled');
    });

    it('turns away an old session token that carries no role', async () => {
      const res = await call('GET', `/${ids.memberA}`, sessionFor(ids.memberA, { role: null }));

      expect(res.status).to.equal(401);
    });

    it('turns away a token signed with the wrong key', async () => {
      const forged = jwt.sign(
        { userId: ids.adminA, orgId: orgA, role: 'admin', email: 'ada@a.test' },
        'not-the-server-secret',
      );

      const res = await call('GET', `/${ids.memberA}`, forged);

      expect(res.status).to.equal(401);
    });

    it('answers /me/role with the live role from the database, not the token', async () => {
      users.get(ids.memberA)!.role = 'admin';

      const res = await call('GET', '/me/role', sessionFor(ids.memberA, { role: 'member' }));

      expect(res.status).to.equal(200);
      expect(res.body).to.deep.equal({ role: 'admin' });
    });
  });

  describe('internal service routes trust only the service token', () => {
    it("will not vouch for a member on the strength of an admin's token", async () => {
      const token = iamUserLookupJwtGenerator(ids.adminA, orgA, SCOPED_SECRET);

      const res = await call('GET', `/internal/${ids.memberA}/adminCheck`, token);

      expect(res.status).to.equal(400);
      expect(errorMessage(res)).to.equal('Admin access required');
    });

    it('refuses the admin check for a member and passes it for an admin', async () => {
      const member = await call(
        'GET',
        `/internal/${ids.memberA}/adminCheck`,
        iamUserLookupJwtGenerator(ids.memberA, orgA, SCOPED_SECRET),
      );
      const admin = await call(
        'GET',
        `/internal/${ids.adminA}/adminCheck`,
        iamUserLookupJwtGenerator(ids.adminA, orgA, SCOPED_SECRET),
      );

      expect(member.status).to.equal(400);
      expect(admin.status).to.equal(200);
    });

    it("does not look up a user from another org than the token's", async () => {
      const token = iamUserLookupJwtGenerator(ids.adminA, orgA, SCOPED_SECRET);

      const res = await call('GET', `/internal/${ids.memberB}`, token);

      expect(res.status).to.equal(404);
    });

    it("lists only the token org's admins, whatever the query string says", async () => {
      const token = iamUserLookupJwtGenerator(ids.adminA, orgA, SCOPED_SECRET);

      const res = await call('GET', `/internal/admin-users?orgId=${orgB}`, token);

      expect(res.status).to.equal(200);
      expect([...(res.body as { adminUserIds: string[] }).adminUserIds].sort()).to.deep.equal([ids.adminA, ids.secondAdminA].sort());
    });

    it('does not accept a user session token on an internal route', async () => {
      const res = await call('GET', `/internal/${ids.memberA}`, sessionFor(ids.adminA));

      expect(res.status).to.equal(401);
    });
  });

  describe('reloading the app config', () => {
    it('does not send the reloaded secrets back in the response', async () => {
      sinon.stub(appConfigModule, 'loadAppConfig').resolves({
        jwtSecret: 'reloaded-jwt-secret',
        scopedJwtSecret: 'reloaded-scoped-secret',
        cookieSecret: 'reloaded-cookie-secret',
        mongo: { uri: 'mongodb://root:hunter2@mongo:27017', db: 'es' },
      } as unknown as appConfigModule.AppConfig);

      const res = await call(
        'POST',
        '/updateAppConfig',
        fetchConfigJwtGenerator(ids.adminA, orgA, SCOPED_SECRET),
      );

      expect(res.status).to.equal(200);
      expect(res.body).to.deep.equal({ message: 'User configuration updated successfully' });
    });
  });
});
