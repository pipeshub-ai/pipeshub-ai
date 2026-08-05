import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import bcrypt from 'bcryptjs';
import { UserAccountController } from '../../../../src/modules/auth/controller/userAccount.controller';
import { UserCredentials } from '../../../../src/modules/auth/schema/userCredentials.schema';
import { UserActivities } from '../../../../src/modules/auth/schema/userActivities.schema';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';

/**
 * The suspicious-login alert is a best-effort side effect: the account state is
 * already persisted before it is sent. The auth MailService throws on failure
 * (unlike the user_management one, which returns a status code), so an
 * unsent alert must not replace the security response with a mail error.
 */
describe('UserAccountController - suspicious login alert is best effort', () => {
  let controller: UserAccountController;
  let mockMailService: any;
  let mockLogger: any;
  let res: any;
  let next: sinon.SinonStub;

  beforeEach(() => {
    mockLogger = {
      debug: sinon.stub(), info: sinon.stub(),
      warn: sinon.stub(), error: sinon.stub(),
    };
    mockMailService = { sendMail: sinon.stub().resolves({ statusCode: 200 }) };

    controller = new UserAccountController(
      {
        iamBackend: 'http://iam:3000',
        cmBackend: 'http://cm:3001',
        frontendUrl: 'http://frontend:3000',
        scopedJwtSecret: 'secret',
        jwtSecret: 'secret',
      } as any,
      { getUserByEmail: sinon.stub() } as any,
      mockMailService,
      {} as any,
      {} as any,
      mockLogger,
      {} as any,
    );

    res = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub().returnsThis(),
      send: sinon.stub().returnsThis(),
    };
    next = sinon.stub();
  });

  afterEach(() => sinon.restore());

  const setupWrongPassword = (controllerRef: any) => {
    sinon.stub(Org, 'findOne').resolves({ shortName: 'Corp' } as any);
    sinon.stub(Users, 'findOne').resolves({ fullName: 'Test User' } as any);
    sinon.stub(UserActivities, 'create').resolves({} as any);
    sinon.stub(UserCredentials, 'findOne').resolves({
      userId: 'u1',
      orgId: 'o1',
      hashedPassword: 'hashed',
      wrongCredentialCount: 4,
      isBlocked: false,
      save: sinon.stub().resolves(),
    } as any);
    // Drive straight to the wrong-password branch that raises the alert.
    sinon.stub(controllerRef, 'ensureBlockStatus').resolves(false);
    sinon.stub(controllerRef, 'verifyPassword').resolves(false);
    sinon.stub(controllerRef, 'incrementWrongCredentialCount').resolves({
      userId: 'u1',
      orgId: 'o1',
      wrongCredentialCount: 5,
      isBlocked: true,
      save: sinon.stub().resolves(),
    });
  };

  const setupFifthWrongOtp = (controllerRef: any) => {
    sinon.stub(Org, 'findOne').resolves({ shortName: 'Corp' } as any);
    sinon.stub(Users, 'findOne').resolves({ fullName: 'Test User' } as any);
    sinon.stub(UserActivities, 'create').resolves({} as any);
    sinon.stub(UserCredentials, 'findOne').resolves({
      userId: 'u1',
      orgId: 'o1',
      otpValidity: Date.now() + 60_000,
      // A real hash of a different OTP, so bcrypt.compare genuinely misses.
      hashedOTP: bcrypt.hashSync('999999', 4),
      wrongCredentialCount: 4,
      isBlocked: false,
      save: sinon.stub().resolves(),
    } as any);
    sinon.stub(controllerRef, 'ensureBlockStatus').resolves(false);
    sinon.stub(controllerRef, 'incrementWrongCredentialCount').resolves({
      userId: 'u1',
      orgId: 'o1',
      wrongCredentialCount: 5,
      isBlocked: false,
      save: sinon.stub().resolves(),
    });
  };

  const runPasswordLogin = () =>
    (controller as any).authenticateWithPassword(
      { _id: 'u1', orgId: 'o1', email: 'user@example.com' },
      'wrong-password',
      '127.0.0.1',
    );

  const runOtpLogin = () =>
    (controller as any).verifyOTP(
      'u1',
      'o1',
      '000000',
      'user@example.com',
      '127.0.0.1',
    );

  it('raises the security error and sends one alert when mail succeeds', async () => {
    setupWrongPassword(controller);

    let surfaced: Error | undefined;
    try { await runPasswordLogin(); } catch (e) { surfaced = e as Error; }

    expect(surfaced!.message).to.contain('Incorrect password');
    expect(mockMailService.sendMail.calledOnce).to.be.true;
  });


  it('still raises the security error when the alert mail fails', async () => {
    setupFifthWrongOtp(controller);
    mockMailService.sendMail.rejects(new Error('timeout of 30000ms exceeded'));

    let surfaced: Error | undefined;
    try { await runOtpLogin(); } catch (e) { surfaced = e as Error; }

    expect(surfaced, 'a security error must still be raised').to.exist;
    expect(surfaced!.message).to.not.contain('timeout of 30000ms');
    expect(surfaced!.message).to.contain('Account Blocked');
    expect(mockLogger.error.called).to.be.true;
    expect(mockMailService.sendMail.callCount).to.equal(1);
  });
});
