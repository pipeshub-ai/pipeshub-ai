import bcrypt from 'bcryptjs';
import { randomBytes } from 'crypto';
import jwt from 'jsonwebtoken';
import { Request, Response, NextFunction } from 'express';

import {
  entityUserWriteJwtGenerator,
  iamJwtGenerator,
  iamUserLookupJwtGenerator,
  jwtGeneratorForForgotPasswordLink,
  mailJwtGenerator,
  passwordResetLinkLifetime,
  refreshTokenJwtGenerator,
} from '../../../libs/utils/createJwt';
import { isDuplicateKeyError } from '../../../libs/utils/mongo.utils';
import { generateOtp } from '../utils/generateOtp';

import { passwordValidator } from '../utils/passwordValidator';
import { getBrandConfig } from '../../../libs/branding';

import {
  AuthMethodType,
  OrgAuthConfig,
} from '../schema/orgAuthConfiguration.schema';
import {
  UsedPasswordResetLink,
  hashResetLink,
} from '../schema/usedPasswordResetLink.schema';
import {
  activityEndsSession,
  SESSION_INVALIDATING_ACTIVITIES,
  userActivitiesType,
} from '../../../libs/utils/userActivities.utils';
import { UserActivities } from '../schema/userActivities.schema';
import {
  AuthenticatedUserRequest,
  AuthenticatedServiceRequest,
} from '../../../libs/middlewares/types';
import { IUserCredentials, UserCredentials } from '../schema/userCredentials.schema';

import { AuthSessionRequest } from '../middlewares/types';

import { SessionData, SessionService } from '../services/session.service';
import mongoose from 'mongoose';
import { OAuth2Client } from 'google-auth-library';
import {
  MicrosoftSignInConfig,
  microsoftAccountIdentity,
  validateAzureAdUser,
} from '../utils/azureAdTokenValidation';
import { IamService } from '../services/iam.service';
import { MailService } from '../services/mail.service';

import {
  BadRequestError,
  ConflictError,
  ForbiddenError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { BaseError } from '../../../libs/errors/base.error';
import {
  markClientSafe,
  serverFailureMessage,
} from '../../../libs/errors/reader-friendly';
import { inject, injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { generateAuthToken } from '../utils/generateAuthToken';
import { recordEvent } from '../../../libs/services/telemetry/event-buffer';
import { domainFromEmail } from '../../../libs/services/telemetry/identity';
import {
  ConfigurationManagerService,
  GOOGLE_AUTH_CONFIG_PATH,
  MICROSOFT_AUTH_CONFIG_PATH,
} from '../services/cm.service';
import { withoutRemovedSignInMethods } from '../utils/removedSignInMethods';
import { AppConfig } from '../../tokens_manager/config/config';
import { Org } from '../../user_management/schema/org.schema';
import { Users } from '../../user_management/schema/users.schema';
import {
  EntitiesEventProducer,
  Event,
  EventType,
  UserUpdatedEvent,
} from '../../user_management/services/entity_events.service';
import { verifyTurnstileToken } from '../../../libs/utils/turnstile-verification';
import { JitProvisioningService } from '../services/jit-provisioning.service';
import {
  assertMethodAllowedAtStep,
  IOrgAuthConfigLike,
} from '../utils/authMethodGuard';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  executeConnectorCommand,
  handleBackendError,
} from '../../tokens_manager/utils/connector.utils';

const {
  LOGIN,
  LOGOUT,
  OTP_GENERATE,
  WRONG_OTP,
  WRONG_PASSWORD,
  REFRESH_TOKEN,
  PASSWORD_CHANGED,
  ACCOUNT_BLOCKED,
  ACCOUNT_DELETED,
} = userActivitiesType;
export const SALT_ROUNDS = 10;
export const ACCOUNT_NO_LONGER_ACTIVE =
  'Your account is no longer active. Contact your admin.';

// The users service answers with a JSON user document; anything else means no account.
const isAccountRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const BLOCK_COOLDOWN_DURATION_MS = 24 * 60 * 60 * 1000;
export const RESET_LINK_ALREADY_USED =
  'This reset link has already been used. Request a new one from the sign-in page.';
export const RESET_LINK_NOT_CHECKED =
  "We couldn't reset your password just now, and nothing was changed. Please try again in a moment.";
// The longest-lived link (a new account's first password) lasts 48 hours.
const RESET_LINK_FALLBACK_LIFETIME_MS = 48 * 60 * 60 * 1000;
const MAX_WRONG_CREDENTIAL_ATTEMPTS = 5;

export const SIGN_IN_SESSION_EXPIRED =
  'Your sign-in session expired. Start again from the sign-in page.';
export const SESSION_NO_LONGER_VALID =
  'Your session is no longer valid. Please sign in again.';
export const OTP_SEND_FAILED =
  "We couldn't send your sign-in code. Wait a minute and try again, or use another sign-in method.";
export const OTP_ALREADY_USED =
  'That sign-in code has already been used. Request a new code and try again.';
export const EMAIL_MISMATCH =
  "You signed in with a different account than the email you entered. Sign in with the matching account, or go back and enter that account's email.";
export const PROVIDER_SHARED_NO_EMAIL =
  "Your sign-in provider didn't share an email address, so we couldn't sign you in. Ask your admin to allow the email permission for PipesHub.";
export const ADMIN_ONLY_SIGN_IN_SETTINGS =
  'Only workspace admins can view or change sign-in settings.';
export const SIGN_IN_ACCOUNT_CHANGED =
  'This step was completed with a different account than the step before it. Start again from the sign-in page and use the same account for every step.';
export const WRONG_EMAIL_OR_PASSWORD =
  'The email or password is incorrect. Check both and try again, or use Forgot password to set a new password.';
export const WRONG_SIGN_IN_CODE =
  "That sign-in code isn't right. Check the most recent code in your email, or request a new one.";
export const SIGN_IN_CODE_REQUESTED =
  'If that email can sign in with a code, one is being sent. It works for 10 minutes. If nothing arrives, check your spam folder or try again later.';

let decoyHash: Promise<string> | undefined;

// Refusals that have no stored hash to check still pay for one bcrypt
// comparison, so an unknown email doesn't answer noticeably faster than a
// real account.
async function compareWithDecoyHash(candidate: unknown): Promise<void> {
  decoyHash ??= bcrypt.hash(randomBytes(16).toString('hex'), SALT_ROUNDS);
  const hash = await decoyHash;
  await bcrypt.compare(typeof candidate === 'string' ? candidate : '', hash);
}

@injectable()
export class UserAccountController {
  constructor(
    @inject('AppConfig') protected config: AppConfig,
    @inject('IamService') protected iamService: IamService,
    @inject('MailService') protected mailService: MailService,
    @inject('SessionService') protected sessionService: SessionService,
    @inject('ConfigurationManagerService')
    protected configurationManagerService: ConfigurationManagerService,
    @inject('Logger') protected logger: Logger,
    @inject('JitProvisioningService') protected jitProvisioningService: JitProvisioningService,
    @inject('EntitiesEventProducer') protected eventService: EntitiesEventProducer,
  ) { }

  /**
   * If the verified token contains an 'email' claim (e.g. the mail attribute in Entra ID)
   * that differs from the stored/session email (which may be the UPN), correct it.
   * When a userId is provided the DB record is updated; the in-memory object is always mutated.
   */
  protected async correctEmailFromToken(
    decodedToken: Record<string, any>,
    target: Record<string, any>,
    context: string,
    emailClaimTrusted: boolean,
  ): Promise<void> {
    const tokenEmail =
      typeof decodedToken.email === 'string' ? decodedToken.email : undefined;
    const targetEmail =
      typeof target.email === 'string' ? target.email.toLowerCase() : '';
    if (
      !emailClaimTrusted ||
      tokenEmail === undefined ||
      tokenEmail === '' ||
      tokenEmail.toLowerCase() === targetEmail
    ) {
      return;
    }
    // Only rename an account that this token signs in by its UPN; never move
    // another account's email.
    const signInNames = [decodedToken.preferred_username, decodedToken.upn]
      .filter((name): name is string => typeof name === 'string')
      .map((name) => name.toLowerCase());
    if (!signInNames.includes(targetEmail)) {
      return;
    }
    if (target._id) {
      try {
        await Users.updateOne(
          { _id: target._id },
          { $set: { email: tokenEmail.toLowerCase() } },
        );
        this.logger.info(`${context}: Corrected user email from UPN to mail attribute`, {
          userId: target._id,
          oldEmail: target.email,
          newEmail: tokenEmail.toLowerCase(),
        });
      } catch (emailUpdateError) {
        // Email update may fail if another user already has this email (unique constraint).
        // Log and continue — the user can still log in with their current email.
        this.logger.warn(`${context}: Could not correct user email`, {
          userId: target._id,
          oldEmail: target.email,
          newEmail: tokenEmail.toLowerCase(),
          error: emailUpdateError,
        });
        return;
      }
    } else {
      this.logger.info(`${context}: Using email from token instead of login email`, {
        loginEmail: target.email,
        tokenEmail,
      });
    }
    target.email = tokenEmail.toLowerCase();
  }

  // A later sign-in step must prove the account an earlier step already proved.
  protected assertSameAccountAsEarlierSteps(
    sessionInfo: SessionData,
    user: Record<string, unknown> | null | undefined,
  ): void {
    const id = user?._id;
    const userId =
      id instanceof mongoose.Types.ObjectId
        ? id.toHexString()
        : typeof id === 'string'
          ? id
          : '';
    if (Number(sessionInfo.currentStep) > 0 && userId !== sessionInfo.userId) {
      throw new UnauthorizedError(SIGN_IN_ACCOUNT_CHANGED);
    }
  }

  async generateHashedOTP() {
    const otp = generateOtp();
    const hashedOTP = await bcrypt.hash(otp, SALT_ROUNDS);

    return { otp, hashedOTP };
  }

  protected getBlockedUntilIso(userCredentials: IUserCredentials): string | undefined {
    const blockExpiresAt = userCredentials.blockExpiresAt;
    if (!blockExpiresAt) {
      return undefined;
    }

    const blockedUntilDate = blockExpiresAt;
    if (Number.isNaN(blockedUntilDate.getTime())) {
      return undefined;
    }

    return blockedUntilDate.toISOString();
  }

  protected async ensureBlockStatus(
    userCredentials: IUserCredentials,
  ): Promise<boolean> {
    if (!userCredentials.isBlocked) {
      return false;
    }

    const now = Date.now();
    const blockExpiresAtTime = userCredentials.blockExpiresAt?.getTime() ?? null;

    if (blockExpiresAtTime && blockExpiresAtTime <= now) {
      userCredentials.isBlocked = false;
      userCredentials.wrongCredentialCount = 0;
      userCredentials.blockExpiresAt = null;
      await userCredentials.save();
      return false;
    }

    return true;
  }

  // Not awaited: waiting for the mail service would make the attempt that
  // locks an account slower than any other refusal, and a failed send must
  // not turn that refusal into a server error.
  protected notifyAccountLocked(
    email: string,
    userId: string,
    orgId: string,
  ): void {
    void (async () => {
      try {
        const org = await Org.findOne({ _id: orgId, isDeleted: false });
        const user = await Users.findOne({
          _id: userId,
          orgId,
          isDeleted: false,
        });
        await this.mailService.sendMail({
          emailTemplateType: 'suspiciousLoginAttempt',
          initiator: {
            jwtAuthToken: mailJwtGenerator(email, this.config.scopedJwtSecret),
            orgId,
          },
          usersMails: [email],
          subject: 'Alert : Suspicious Login Attempt Detected',
          templateData: {
            orgName: org?.shortName || org?.registeredName,
            name: user?.fullName,
          },
        });
      } catch (error) {
        this.logger.error("The account-locked email couldn't be sent", {
          userId,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    })();
  }

   async verifyOTP(
    userId: string,
    orgId: string,
    inputOTP: any,
    email: string,
    ipAddress: string,
  ) {
    const userCredentials = await UserCredentials.findOne({
      userId,
      orgId,
      isDeleted: false,
    });
    if (!userCredentials) {
      await compareWithDecoyHash(inputOTP);
      throw new UnauthorizedError(WRONG_SIGN_IN_CODE);
    }
    // A locked account, a missing code and an expired code are all answered
    // like a wrong code, since an unknown email can only ever get that answer.
    // The owner learns about a lock from the email sent when it was applied.
    const locked = await this.ensureBlockStatus(userCredentials);
    if (
      locked ||
      !userCredentials.otpValidity ||
      !userCredentials.hashedOTP ||
      Date.now() > userCredentials.otpValidity
    ) {
      if (locked) {
        this.logger.warn('Sign-in code refused: the account is locked', {
          userId,
        });
      }
      await compareWithDecoyHash(inputOTP);
      throw new UnauthorizedError(WRONG_SIGN_IN_CODE);
    }

    // The attempt is counted before the code is compared. Counting it
    // afterwards lets every request already in flight be compared before the
    // first of them has raised the counter, so a burst gets past the limit.
    const attempt = await this.reserveCredentialAttempt(userId, orgId);
    if (!attempt?.hashedOTP) {
      await compareWithDecoyHash(inputOTP);
      throw new UnauthorizedError(WRONG_SIGN_IN_CODE);
    }

    // Ensure OTP is a string for bcrypt.compare (bcrypt requires both arguments to be strings)
    const otpString = String(inputOTP);
    const isMatching = await bcrypt.compare(otpString, attempt.hashedOTP);
    this.logger.debug('isMatching', isMatching);
    if (!isMatching) {
      await UserActivities.create({
        email: email,
        activityType: WRONG_OTP,
        ipAddress: ipAddress,
        loginMode: 'OTP',
      });
      if (attempt.wrongCredentialCount >= MAX_WRONG_CREDENTIAL_ATTEMPTS) {
        this.logger.warn('blocked', email);
        attempt.isBlocked = true;
        attempt.blockExpiresAt = new Date(Date.now() + BLOCK_COOLDOWN_DURATION_MS);
        await attempt.save();
        await UserActivities.create({
          userId: userId,
          orgId: orgId,
          email: email,
          activityType: ACCOUNT_BLOCKED,
          ipAddress: ipAddress,
          loginMode: 'OTP',
        });

        this.notifyAccountLocked(email, userId, orgId);
      }
      throw new UnauthorizedError(WRONG_SIGN_IN_CODE);
    }

    // Clearing the code in the same write that matches it makes it single-use,
    // even when two requests race with the same code.
    const claimed = await UserCredentials.findOneAndUpdate(
      { userId, orgId, isDeleted: false, hashedOTP: attempt.hashedOTP },
      { $unset: { hashedOTP: '', otpValidity: '' } },
      { returnDocument: 'after' },
    );
    if (!claimed) {
      throw new UnauthorizedError(OTP_ALREADY_USED);
    }
    // After the claim, and separate from it: tying the claim to the counter
    // would let another request's reservation stop a correct code being used.
    await this.releaseCredentialAttempt(
      userId,
      orgId,
      attempt.wrongCredentialCount,
    );

    return { statusCode: 200 };
  }

  async verifyPassword(password: string, hashedPassword: string) {
    return bcrypt.compare(password, hashedPassword);
  }

  // Shared by the code and the password sign-in. Null once the account is
  // locked or has no attempts left.
  async reserveCredentialAttempt(userId: string, orgId: string) {
    return UserCredentials.findOneAndUpdate(
      {
        userId,
        orgId,
        isDeleted: false,
        isBlocked: { $ne: true },
        wrongCredentialCount: { $lt: MAX_WRONG_CREDENTIAL_ATTEMPTS },
      },
      { $inc: { wrongCredentialCount: 1 } },
      { returnDocument: 'after' },
    );
  }

  // Resets the counter after a successful sign-in, but only while it still
  // reads what this request's reservation left it at and the account is not
  // locked. Otherwise a success would wipe out attempts that other requests
  // reserved in the meantime.
  async releaseCredentialAttempt(
    userId: string,
    orgId: string,
    reservedCount: number,
  ) {
    await UserCredentials.updateOne(
      {
        userId,
        orgId,
        isDeleted: false,
        isBlocked: { $ne: true },
        wrongCredentialCount: reservedCount,
      },
      { $set: { wrongCredentialCount: 0 } },
    );
  }

  async incrementWrongCredentialCount(userId: string, orgId: string) {
    const userCredentials = await UserCredentials.findOneAndUpdate(
      { userId, orgId, isDeleted: false },
      { $inc: { wrongCredentialCount: 1 } },
      { returnDocument: 'after' },
    );

    return userCredentials;
  }
  // userValidator verifies the JWT and sets req.user; it does not bind an org context.
  private callerIdentity(req: AuthSessionRequest): {
    userId: string;
    orgId: string;
  } {
    const userId = String(req.user?.userId ?? '');
    const orgId = String(req.user?.orgId ?? '');
    if (!userId || !orgId) {
      throw new UnauthorizedError(SESSION_NO_LONGER_VALID);
    }
    return { userId, orgId };
  }

  async getPasswordStatus(
    req: AuthSessionRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { userId, orgId } = this.callerIdentity(req);
      const userCredentialData = await UserCredentials.findOne({
        userId,
        orgId,
        isDeleted: false,
      });
      res.json({ hasPassword: !!userCredentialData?.hashedPassword });
    } catch (error) {
      next(error);
    }
  }

  // Users who signed in only through SSO have no password. They set one through the
  // same emailed single-use link as a forgotten password, so the inbox is the proof.
  async sendPasswordSetupEmail(
    req: AuthSessionRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { userId, orgId } = this.callerIdentity(req);
      const userCredentialData = await UserCredentials.findOne({
        userId,
        orgId,
        isDeleted: false,
      });
      if (userCredentialData?.hashedPassword) {
        throw new BadRequestError(
          'A password is already set. Use change password instead.',
        );
      }
      const userFindResult = await this.iamService.getUserById(
        userId,
        iamUserLookupJwtGenerator(userId, orgId, this.config.scopedJwtSecret),
      );
      if (userFindResult.statusCode !== 200) {
        throw new NotFoundError(SESSION_NO_LONGER_VALID);
      }
      await this.sendForgotPasswordEmail(
        userFindResult.data as Record<string, unknown>,
      );
      res.status(200).send({ data: 'mail sent' });
    } catch (error) {
      next(error);
    }
  }

  async hasPasswordMethod(
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.tokenPayload?.orgId;
      const orgAuthConfig = await OrgAuthConfig.findOne({ orgId })
        .select('authSteps')
        .lean<{ authSteps?: { order: number; allowedMethods: { type: string }[] }[] }>();
      // Read like sign-in does: a policy saved before 1.0 that named a removed
      // method already offers password, before the migration writes it.
      const isPasswordAuthEnabled =
        !!orgAuthConfig &&
        withoutRemovedSignInMethods(orgAuthConfig.authSteps).steps.some((step) =>
          step.allowedMethods.some((m) => m.type === 'password'),
        );
      res.json({
        isPasswordAuthEnabled,
      });
    } catch (error) {
      next(error);
    }
  }

  async initAuth(
    req: AuthSessionRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const email = req.body?.email || "";

      const org = await Org.findOne({ isDeleted: false });
      const orgAuthConfig = org
        ? await OrgAuthConfig.findOne({ orgId: org._id, isDeleted: false })
        : null;

      const newUser = { orgId: org?._id, email: "", _id: "" };

      // A policy saved before 1.0 may still name a removed method until the
      // startup migration rewrites it.
      const authSteps = withoutRemovedSignInMethods(orgAuthConfig?.authSteps).steps;
      const dbMethods = authSteps[0]?.allowedMethods.map((m) => m.type) ?? [];

      const allowedMethods: string[] = [];      // Methods to show on frontend
      const jitEnabledMethods: string[] = [];   // Only methods with JIT actually ON
      const authProviders: Record<string, any> = {};
      const jitConfig: Record<string, boolean> = {};

      const configMethodMap: Record<string, { path: string, key: string }> = {
        'google': { path: GOOGLE_AUTH_CONFIG_PATH, key: 'google' },
        'microsoft': { path: MICROSOFT_AUTH_CONFIG_PATH, key: 'microsoft' },
      };

      if (orgAuthConfig) {
        for (const method of dbMethods) {
          // CASE 1: Static methods (otp, password)
          // These are allowed methods, but NEVER trigger jitEnabled flags
          if (method === 'otp' || method === 'password') {
            allowedMethods.push(method);
            continue;
          }

          // CASE 2: Config-based methods
          const mapping = configMethodMap[method];
          if (!mapping) continue;

          try {
            const configResponse = await this.configurationManagerService.getConfig(
              this.config.cmBackend,
              mapping.path,
              newUser,
              this.config.scopedJwtSecret,
            );

            const configData = configResponse.data;

            // Always add to allowed list if config exists
            allowedMethods.push(method);
            authProviders[mapping.key] = configData;

            if (configData?.enableJit === true) {
              jitEnabledMethods.push(method);
              jitConfig[mapping.key] = true;
            }
          } catch (e) {
            this.logger.debug(`${method} config fetch failed, not adding to allowed methods`);
          }
        }
      }

      // Final check: if no methods from DB were valid/found, fallback to password
      const finalMethods = allowedMethods.length > 0 ? allowedMethods : ['password'];

      const session = await this.sessionService.createSession({
        userId: 'NOT_FOUND',
        email: email,
        orgId: orgAuthConfig ? orgAuthConfig.orgId.toString() : '',
        authConfig: authSteps,
        currentStep: 0,

        jitConfig: jitEnabledMethods.length > 0 ? jitConfig : undefined,
      });

      if (session?.token) res.setHeader('x-session-token', session.token);

      res.json({
        currentStep: 0,
        allowedMethods: finalMethods,
        message: 'Authentication initialized',
        authProviders,
        jitEnabled: jitEnabledMethods.length > 0,
      });

    } catch (error) {
      next(error);
    }
  }

  async sendForgotPasswordEmail(user: Record<string, any>) {
    try {
      const { passwordResetToken, mailAuthToken } =
        jwtGeneratorForForgotPasswordLink(
          user.email,
          user._id,
          user.orgId,
          this.config.scopedJwtSecret,
        );
      const resetPasswordLink = `${this.config.frontendUrl}/reset-password#token=${passwordResetToken}`;
      const org = await Org.findOne({ _id: user.orgId, isDeleted: false });
      await this.mailService.sendMail({
        emailTemplateType: 'resetPassword',
        initiator: { jwtAuthToken: mailAuthToken, orgId: user.orgId?.toString() },
        usersMails: [user.email],
        subject: `${getBrandConfig().productName} | Reset your password!`,
        templateData: {
          orgName: org?.shortName || org?.registeredName,
          name: user.fullName,
          link: resetPasswordLink,
          linkLifetime: passwordResetLinkLifetime().description,
        },
      });

      return {
        statusCode: 200,
        data: 'mail sent',
      };
    } catch (error) {
      throw error;
    }
  }

  async isPasswordSame(newPassword: string, currentHashedPassword: string) {
    if (!newPassword || !currentHashedPassword) {
      throw new BadRequestError(
        'Both new password and current hashed password are required',
      );
    }
    // Use bcrypt.compare to check if the new password matches the current hash
    const isSame = await bcrypt.compare(newPassword, currentHashedPassword);

    return isSame;
  }

  async updatePassword(
    userId: string,
    orgId: string,
    newPassword: string,
    ipAddress: string,
    onPasswordSaved?: () => void,
  ) {
    try {
      const isPasswordValid = passwordValidator(newPassword);
      if (!isPasswordValid) {
        throw new BadRequestError(
          'Password should have minimum 8 characters with at least one uppercase, one lowercase, one number, and one special character, and be no longer than 72 bytes.',
        );
      }
      let userCredentialData = await UserCredentials.findOne({
        userId: userId,
        orgId: orgId,
        isDeleted: false,
      });

      if (userCredentialData && (await this.ensureBlockStatus(userCredentialData))) {
        const blockedUntil = this.getBlockedUntilIso(userCredentialData);
        throw new BadRequestError(
          blockedUntil
            ? `You cannot change you password as your account is blocked due to multiple incorrect logins [blockedUntil:${blockedUntil}]`
            : 'You cannot change you password as your account is blocked due to multiple incorrect logins',
        );
      }
      if (!userCredentialData) {
        userCredentialData = new UserCredentials();
        userCredentialData.orgId = orgId;
        userCredentialData.userId = userId;
      }

      if (
        userCredentialData.hashedPassword &&
        (await this.isPasswordSame(
          newPassword,
          userCredentialData.hashedPassword,
        ))
      ) {
        throw new BadRequestError('Old and new password cannot be same');
      }

      const hashedPassword = await bcrypt.hash(newPassword, SALT_ROUNDS);

      userCredentialData.hashedPassword = hashedPassword;
      if (ipAddress) {
        userCredentialData.ipAddress = ipAddress;
      }
      await userCredentialData.save();
      onPasswordSaved?.();

      await UserActivities.create({
        orgId: orgId,
        userId: userId,
        activityType: PASSWORD_CHANGED,
        ipAddress: ipAddress,
      });

      return { statusCode: 200, data: 'password updated' };
    } catch (error) {
      throw error;
    }
  }

  forgotPasswordEmail = async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    // Always responds 200 with the same generic message regardless of whether
    // the email is registered, so the response cannot be used to enumerate
    // accounts. Validation and CAPTCHA failures still surface as errors.
    const genericResponse = {
      data: 'If an account exists for this email, a password reset link has been sent.',
    };

    try {
      const { email, 'cf-turnstile-response': turnstileToken } = req.body;
      if (!email) {
        throw new BadRequestError('Email is required');
      }

      const turnstileSecretKey = process.env.TURNSTILE_SECRET_KEY;
      if (turnstileSecretKey) {
        const isValid = await verifyTurnstileToken(
          turnstileToken,
          turnstileSecretKey,
          req.ip,
          this.logger,
        );
        if (!isValid) {
          throw new UnauthorizedError('Invalid CAPTCHA verification. Please try again.');
        }
      }

      res.status(200).send(genericResponse);
      // Lookup and mail run after the reply so latency does not reveal whether the account exists.
      void this.sendResetIfAccountExists(email);
      return;
    } catch (error) {
      next(error);
    }
  };

  private async sendResetIfAccountExists(email: string): Promise<void> {
    try {
      const authToken = iamJwtGenerator(email, this.config.scopedJwtSecret);
      const user = await this.iamService.getUserByEmail(email, authToken);

      if (user.statusCode === 200 && user.data) {
        await this.sendForgotPasswordEmail(user.data);
      }
    } catch (lookupOrSendError) {
      this.logger.warn('forgotPasswordEmail: suppressing internal error', {
        error:
          lookupOrSendError instanceof Error
            ? lookupOrSendError.message
            : lookupOrSendError,
      });
    }
  }
  async setUpAuthConfig(req: AuthSessionRequest, res: Response): Promise<void> {
    try {
      // Check if an org auth config already exists (excluding deleted ones)
      const count = await OrgAuthConfig.countDocuments({ isDeleted: false });

      if (count > 0) {
        res.status(200).json({ message: 'Org config already done' });
        return;
      }

      let session: mongoose.ClientSession | null = null;
      const {
        contactEmail,
        registeredName,
        adminFullName,
        sendEmail = false,
      } = req.body;

      // Create organization
      const orgData = {
        contactEmail,
        registeredName,
        adminFullName,
        sendEmail,
      };
      const result = await this.iamService.createOrg(orgData, '');

      if (!result || !result.data) {
        res.status(500).json({ message: 'Internal server error' });
        return;
      }

      const { _id: orgId, domain } = result.data;

      // Create new org authentication config
      const orgAuth = new OrgAuthConfig({
        orgId,
        domain,
        authSteps: [
          {
            order: 1,
            allowedMethods: [{ type: 'password' }],
          },
        ],
        isDeleted: false,
      });

      // Start transaction if a replica set is available
      session = await mongoose.startSession();
      try {
        if (this.config.rsAvailable === 'true') {
          session.startTransaction();
          await orgAuth.save({ session });
          await session.commitTransaction();
        } else {
          await orgAuth.save();
        }

        res
          .status(201)
          .json({ message: 'Org Auth Config created successfully' });
        return;
      } catch (saveError) {
        if (session) await session.abortTransaction();
        throw saveError;
      } finally {
        if (session) session.endSession();
      }
    } catch (error) {
      throw error;
    }
  }
  x509ToBase64(certString: string) {
    const buffer = Buffer.from(certString, 'utf-8'); // Convert string to Buffer
    return buffer.toString('base64'); // Convert to Base64
  }
  async getAuthMethod(
    req: AuthSessionRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      if (!req.user) {
        throw new BadRequestError('User not authenticated');
      }

      const orgId = req.user.orgId;
      const userId = req.user.userId;

      const adminCheckResult = await this.iamService.checkAdminUser(
        userId,
        iamUserLookupJwtGenerator(
          userId,
          orgId,
          this.config.scopedJwtSecret,
        ),
      );

      if (adminCheckResult.statusCode !== 200) {
        throw new NotFoundError(ADMIN_ONLY_SIGN_IN_SETTINGS);
      }

      if (!orgId) {
        throw new BadRequestError('OrgId are required');
      }

      // Fetch organization's authentication config
      const orgAuthConfig = await OrgAuthConfig.findOne({ orgId });

      if (!orgAuthConfig) {
        throw new NotFoundError('Organisation config not found');
      }
      const authMethod = withoutRemovedSignInMethods(orgAuthConfig.authSteps).steps;

      res.status(200).json({ authMethods: authMethod });
    } catch (error) {
      next(error);
    }
  }
  async updateAuthMethod(
    req: AuthSessionRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { authMethod } = req.body; // Get auth method and organization ID from request
      if (!req.user) {
        throw new UnauthorizedError('User not authenticated');
      }

      const orgId = req.user.orgId;
      const userId = req.user.userId;

      const adminCheckResult = await this.iamService.checkAdminUser(
        userId,
        iamUserLookupJwtGenerator(
          userId,
          orgId,
          this.config.scopedJwtSecret,
        ),
      );

      if (adminCheckResult.statusCode !== 200) {
        throw new NotFoundError(ADMIN_ONLY_SIGN_IN_SETTINGS);
      }

      if (!authMethod) {
        throw new BadRequestError('Auth method is required');
      }

      // Fetch organization's authentication config
      const orgAuthConfig = await OrgAuthConfig.findOne({ orgId });

      if (!orgAuthConfig) {
        throw new NotFoundError('Organization config not found');
      }
      orgAuthConfig.authSteps = authMethod;
      await orgAuthConfig.save();

      res.status(200).json({ message: 'Auth method updated', authMethod });
    } catch (error) {
      next(error);
    }
  }

  async resetPasswordViaEmailLink(
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { password } = req.body;
      if (!password) {
        throw new BadRequestError('password is required');
      }
      const orgId = req.tokenPayload?.orgId;
      const userId = req.tokenPayload?.userId;
      const linkHash = await this.claimResetLink(req);
      let passwordSaved = false;
      try {
        const userFindResult = await this.iamService.getUserById(
          userId,
          iamUserLookupJwtGenerator(userId, orgId, this.config.scopedJwtSecret),
        );

        if (userFindResult.statusCode !== 200) {
          throw new NotFoundError(SESSION_NO_LONGER_VALID);
        }
        await this.updatePassword(userId, orgId, password, req.ip!, () => {
          passwordSaved = true;
        });
      } catch (error) {
        // Until the new password is saved nothing has changed, so the link is
        // handed back for another attempt. Once it is saved the link is spent,
        // even if recording the change fails afterwards.
        if (!passwordSaved) {
          await this.releaseResetLink(linkHash);
        }
        throw error;
      }

      res.status(200).send({ data: 'password reset' });
      return;
    } catch (error) {
      next(error);
    }
  }

  /**
   * Mark the link used before any reset work, so of two simultaneous requests
   * only one proceeds. The PASSWORD_CHANGED check in scopedTokenValidator stops
   * a later reuse, but that activity is written only when a reset finishes.
   */
  protected async claimResetLink(
    req: AuthenticatedServiceRequest,
  ): Promise<string> {
    // The token the middleware verified, not a re-parse of the header: two
    // spellings of one header must be one link.
    const token = req.verifiedToken ?? '';
    if (token === '') {
      throw new UnauthorizedError('No token provided');
    }
    const linkHash = hashResetLink(token);
    const exp: unknown = req.tokenPayload?.exp;
    const expiresAt = new Date(
      typeof exp === 'number'
        ? exp * 1000
        : Date.now() + RESET_LINK_FALLBACK_LIFETIME_MS,
    );

    try {
      // Mongoose builds indexes in the background; without the unique index
      // both inserts would succeed, so a link could be used twice.
      await UsedPasswordResetLink.init();
    } catch (initError) {
      // init() keeps its first rejection for the life of the process, so a
      // failed build is retried here rather than refusing every later reset.
      try {
        await UsedPasswordResetLink.ensureIndexes();
      } catch (error) {
        this.logger.error('The used reset link index could not be built', {
          error: error instanceof Error ? error.message : String(error),
          firstError: initError instanceof Error ? initError.message : String(initError),
        });
        throw new ServiceUnavailableError(RESET_LINK_NOT_CHECKED);
      }
    }

    try {
      await UsedPasswordResetLink.create({
        linkHash,
        userId: String(req.tokenPayload?.userId),
        orgId: String(req.tokenPayload?.orgId),
        expiresAt,
      });
    } catch (error) {
      if (isDuplicateKeyError(error)) {
        throw new UnauthorizedError(RESET_LINK_ALREADY_USED);
      }
      throw error;
    }
    return linkHash;
  }

  protected async releaseResetLink(linkHash: string): Promise<void> {
    try {
      await UsedPasswordResetLink.deleteOne({ linkHash });
    } catch (error) {
      // The reset's own error is the one to report; this only costs the person
      // a fresh link.
      this.logger.error('Could not hand back an unused reset link', {
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async resetPassword(
    req: AuthSessionRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { newPassword, currentPassword, 'cf-turnstile-response': turnstileToken } = req.body;

      if (!currentPassword) {
        throw new BadRequestError('currentPassword is required');
      }
      if (!newPassword) {
        throw new BadRequestError('newPassword is required');
      }

      // Verify Turnstile token if secret key is configured
      const turnstileSecretKey = process.env.TURNSTILE_SECRET_KEY;
      if (turnstileSecretKey) {
        const isTurnstileValid = await verifyTurnstileToken(
          turnstileToken,
          turnstileSecretKey
        );
        if (!isTurnstileValid) {
          throw new UnauthorizedError('Invalid CAPTCHA verification. Please try again.');
        }
      }

      const userCredentialData = await UserCredentials.findOne({
        userId: req.user?.userId,
        orgId: req.user?.orgId,
        isDeleted: false,
      });

      if (!userCredentialData) {
        throw new NotFoundError('Previous password not found');
      }
      if (currentPassword === newPassword) {
        throw new BadRequestError('Current and new password cannot be same');
      }

      const isPasswordCorrect = await bcrypt.compare(
        currentPassword,
        userCredentialData?.hashedPassword || ' ',
      );
      if (!isPasswordCorrect) {
        throw new UnauthorizedError('Current password is incorrect.');
      }
      await this.updatePassword(
        req.user?.userId,
        req.user?.orgId,
        newPassword,
        req.ip || ' ',
      );

      const userFindResult = await this.iamService.getUserById(
        req.user?.userId,
        iamUserLookupJwtGenerator(
          req.user?.userId,
          req.user?.orgId,
          this.config.scopedJwtSecret,
        ),
      );

      if (userFindResult.statusCode !== 200) {
        throw new NotFoundError(SESSION_NO_LONGER_VALID);
      }

      const user = userFindResult.data;
      const accessToken = await generateAuthToken(user, this.config.jwtSecret);

      res.status(200).send({
        data: 'password reset',
        accessToken
      });
      return;
    } catch (error) {
      next(error);
    }
  }

  getDomainFromEmail(email: string) {
    if (typeof email !== 'string' || email.trim() === '') {
      return null;
    }

    // Use a regular expression to match the domain part of the email
    const match = email.match(/@([^@]+)$/);

    // If a match is found, return the captured group (domain)
    // Otherwise, return null
    return match ? match[1]?.toLowerCase() : null;
  }

  async generateAndSendLoginOtp(
    userId: string,
    orgId: string,
    userFullName: string,
    email: string,
    ipAddress: string,
  ) {
    const userCredentialData = await UserCredentials.findOne({
      orgId: orgId,
      userId: userId,
      isDeleted: false,
    });
    const org = await Org.findOne({ _id: orgId, isDeleted: false });

    if (userCredentialData && (await this.ensureBlockStatus(userCredentialData))) {
      // Same hashing work as an unknown email, whose request always hashes once.
      await this.generateHashedOTP();
      const blockedUntil = this.getBlockedUntilIso(userCredentialData);
      throw new ForbiddenError(
        blockedUntil
          ? `OTP not sent. You have entered incorrect OTP/Password too many times. Your account has been disabled. [blockedUntil:${blockedUntil}]`
          : 'OTP not sent. You have entered incorrect OTP/Password too many times. Your account has been disabled.',
      );
    }

    const otpValidity = Date.now() + 10 * 60 * 1000;
    const { otp, hashedOTP } = await this.generateHashedOTP();

    if (!userCredentialData) {
      await UserCredentials.create({
        orgId: orgId,
        userId: userId,
        ipAddress: ipAddress,
        hashedOTP: hashedOTP,
        otpValidity: otpValidity,
      });
    } else {
      userCredentialData.hashedOTP = hashedOTP;
      userCredentialData.otpValidity = otpValidity;
      await userCredentialData.save();
    }
    // Not awaited: waiting for the mail service would make a real account's
    // answer slower than an unknown email's. The code is already stored, so it
    // works whenever the email arrives.
    const logSendFailure = (details: Record<string, unknown>): void => {
      this.logger.error("The sign-in code email couldn't be sent", {
        userId,
        ...details,
      });
    };
    this.mailService
      .sendMail({
        emailTemplateType: 'loginWithOTP',
        initiator: {
          jwtAuthToken: mailJwtGenerator(email, this.config.scopedJwtSecret),
          orgId: orgId?.toString(),
        },
        usersMails: [email],
        subject: 'OTP for Login',
        templateData: {
          name: userFullName,
          orgName: org?.shortName || org?.registeredName,
          otp: otp,
        },
      })
      .then((result) => {
        if (result.statusCode !== 200) {
          logSendFailure({ data: result.data });
        }
      })
      .catch((error: unknown) => {
        logSendFailure({
          error: error instanceof Error ? error.message : String(error),
        });
      });
    return { statusCode: 200, data: 'OTP sent' };
  }

  getLoginOtp = async (
    req: AuthSessionRequest,
    res: Response,
  ): Promise<void> => {
    try {
      const { email } = req.body;

      if (!email) {
        throw new BadRequestError('Email is required');
      }

      await UserActivities.create({
        email: email,
        activityType: OTP_GENERATE,
        ipAddress: req.ip,
      });
      const authToken = iamJwtGenerator(email, this.config.scopedJwtSecret);
      const result = await this.iamService.getUserByEmail(email, authToken);
      if (result.statusCode === 404) {
        // Same answer, and the same code hashing, as for a real account.
        await this.generateHashedOTP();
        res.status(200).send(SIGN_IN_CODE_REQUESTED);
        return;
      }
      if (result.statusCode !== 200) {
        this.logger.error('Looking up the account for a sign-in code failed', {
          statusCode: result.statusCode,
          data: result.data,
        });
        throw new InternalServerError(OTP_SEND_FAILED);
      }
      const user = result.data as {
        _id: string;
        orgId: string;
        fullName: string;
      };

      // A locked account or a failed send gets the same answer as an unknown
      // email, so the answer never shows that the account exists.
      try {
        await this.generateAndSendLoginOtp(
          user._id,
          user.orgId,
          user.fullName,
          email,
          req.ip || ' ',
        );
      } catch (sendError) {
        this.logger.warn('No sign-in code was sent', {
          userId: user._id,
          error:
            sendError instanceof Error ? sendError.message : String(sendError),
        });
      }
      res.status(200).send(SIGN_IN_CODE_REQUESTED);
    } catch (error) {
      throw error;
    }
  };

  async getAccessTokenFromRefreshToken(
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.tokenPayload?.orgId;
      const userId = req.tokenPayload?.userId;

      await this.refuseIfSessionEnded(userId, orgId, req.tokenPayload?.iat);

      await UserActivities.create({
        orgId,
        userId,
        activityType: REFRESH_TOKEN,
        ipAddress: req.ip,
      });

      const user = await this.activeAccountForRefresh(userId, orgId);

      const userCredential = await UserCredentials.findOneAndUpdate(
        {
          userId: userId,
          orgId: orgId,
          isDeleted: false,
        },
        {
          $set: {
            lastLogin: Date.now(),
            ipAddress: req.ip,
          },
        },
        { returnDocument: 'after', upsert: true },
      );

      if (!userCredential) {
        throw new NotFoundError(SESSION_NO_LONGER_VALID);
      }

      if (await this.ensureBlockStatus(userCredential as IUserCredentials)) {
        const blockedUntil = this.getBlockedUntilIso(userCredential as IUserCredentials);
        const brand = getBrandConfig();
        const disabled = `Your account has been disabled. If it is a mistake, Please reach out to ${brand.accountContactEmail ?? brand.supportEmail} to get it restored.`;
        throw new BadRequestError(
          blockedUntil
            ? `${disabled} [blockedUntil:${blockedUntil}]`
            : disabled,
        );
      }

      // Again, right before minting: a deletion recorded while this refresh ran
      // would otherwise get a token newer than the deletion.
      await this.refuseIfSessionEnded(userId, orgId, req.tokenPayload?.iat);
      const accessToken = await generateAuthToken(user, this.config.jwtSecret);

      res.status(200).json({ user: user, accessToken: accessToken });
      return;
    } catch (error) {
      next(error);
    }
  }

  /**
   * Refuses a refresh token issued before a sign-out, password change, role
   * change, lock or deletion (the same rule as the access-token middleware).
   */
  protected async refuseIfSessionEnded(
    userId: string | undefined,
    orgId: string | undefined,
    issuedAtSeconds: number | undefined,
  ): Promise<void> {
    if (!userId || !orgId) {
      return;
    }
    let activity: { activityType?: string; createdAt?: Date } | null = null;
    try {
      activity = await UserActivities.findOne({
        userId,
        orgId,
        isDeleted: false,
        activityType: { $in: [...SESSION_INVALIDATING_ACTIVITIES] },
      })
        .sort({ createdAt: -1 })
        .lean()
        .exec();
    } catch (activityError) {
      this.logger.error(
        'Failed to fetch session-invalidating activity on refresh',
        activityError,
      );
      return;
    }
    if (activity && activityEndsSession(activity, issuedAtSeconds)) {
      throw new UnauthorizedError(
        activity.activityType === ACCOUNT_DELETED
          ? ACCOUNT_NO_LONGER_ACTIVE
          : 'Session expired, please login again',
      );
    }
  }

  /**
   * The account a refresh token belongs to, refused when it has been deleted
   * or disabled. The IAM lookup answers a deleted account with statusCode 404.
   */
  protected async activeAccountForRefresh(
    userId: string,
    orgId: string,
  ): Promise<Record<string, unknown>> {
    let result: { statusCode: number; data?: unknown };
    try {
      result = await this.iamService.getUserById(
        userId,
        iamUserLookupJwtGenerator(userId, orgId, this.config.scopedJwtSecret),
      );
    } catch (error) {
      if (error instanceof BaseError) {
        throw error;
      }
      this.logger.error('Looking up the account to refresh a session failed', {
        userId,
        error: error instanceof Error ? error.message : String(error),
      });
      throw markClientSafe(
        new InternalServerError(serverFailureMessage('refresh your session')),
      );
    }

    const user = result.data;
    if (
      result.statusCode !== 200 ||
      !isAccountRecord(user) ||
      user.isDisabled === true
    ) {
      throw new UnauthorizedError(ACCOUNT_NO_LONGER_ACTIVE);
    }
    return user;
  }

  async logoutSession(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;

      await UserActivities.create({
        orgId,
        userId,
        activityType: LOGOUT,
        ipAddress: req.ip,
      });

      res.status(200).end();
      return;
    } catch (error) {
      next(error);
    }
  }

  async authenticateWithPassword(
    user: Record<string, any>,
    password: unknown,
    ip: string,
  ) {
    const userId = user._id;
    const orgId = user.orgId;
    const email = user.email;

    const userCredentials = await UserCredentials.findOne({
      orgId,
      userId,
      isDeleted: false,
    });

    // No password set, no password sent and a locked account are all answered
    // like a wrong password, since an unknown email can only ever get that
    // answer. The owner learns about a lock from the email sent when it was
    // applied.
    const locked =
      !!userCredentials && (await this.ensureBlockStatus(userCredentials));
    if (
      !userCredentials?.hashedPassword ||
      typeof password !== 'string' ||
      locked
    ) {
      if (locked) {
        this.logger.warn('Password sign-in refused: the account is locked', {
          userId: String(userId),
        });
      }
      await compareWithDecoyHash(password);
      throw new BadRequestError(WRONG_EMAIL_OR_PASSWORD);
    }

    // Counted before the compare, for the same reason as a sign-in code.
    const attempt = await this.reserveCredentialAttempt(userId, orgId);
    if (!attempt?.hashedPassword) {
      await compareWithDecoyHash(password);
      throw new BadRequestError(WRONG_EMAIL_OR_PASSWORD);
    }

    const isPasswordCorrect = await this.verifyPassword(
      password,
      attempt.hashedPassword,
    );

    if (!isPasswordCorrect) {
      await UserActivities.create({
        email: email,
        activityType: WRONG_PASSWORD,
        ipAddress: ip,
        loginMode: 'PASSWORD',
      });
      if (attempt.wrongCredentialCount >= MAX_WRONG_CREDENTIAL_ATTEMPTS) {
        attempt.isBlocked = true;
        attempt.blockExpiresAt = new Date(Date.now() + BLOCK_COOLDOWN_DURATION_MS);
        await attempt.save();
        await UserActivities.create({
          userId: userId,
          orgId: orgId,
          email: email,
          activityType: ACCOUNT_BLOCKED,
          ipAddress: ip,
          loginMode: 'PASSWORD',
        });
        this.notifyAccountLocked(String(email), String(userId), String(orgId));
      }
      throw new BadRequestError(WRONG_EMAIL_OR_PASSWORD);
    }
    await this.releaseCredentialAttempt(
      userId,
      orgId,
      attempt.wrongCredentialCount,
    );

    await UserActivities.create({
      orgId: orgId,
      userId,
      activityType: LOGIN,
      ipAddress: ip,
    });

    return {
      statusCode: 200,
    };
  }

  async authenticateWithOtp(
    user: Record<string, any>,
    otp: string,
    ip: string,
  ) {
    const result = await this.verifyOTP(
      user._id,
      user.orgId,
      otp,
      user.email,
      ip,
    );
    this.logger.info('result for otp verification', result);
    if (result.statusCode !== 200) {
      throw new BadRequestError("We couldn't verify that code. Request a new code and try again.");
    }

    const userId = user._id;
    const orgId = user.orgId;

    await UserActivities.create({
      orgId: orgId,
      userId,
      activityType: LOGIN,
      ipAddress: ip,
    });
  }

  async authenticateWithGoogle(
    user: Record<string, any>,
    credential: string,
    ip: string,
  ) {
    const configManagerResponse =
      await this.configurationManagerService.getConfig(
        this.config.cmBackend,
        GOOGLE_AUTH_CONFIG_PATH,
        user,
        this.config.scopedJwtSecret,
      );
    const { clientId } = configManagerResponse.data;

    const client = new OAuth2Client(clientId);

    // Verify the Google ID token
    const ticket = await client.verifyIdToken({
      idToken: credential,
      audience: clientId, // Ensure it matches your client ID
    });

    const payload = ticket.getPayload();
    if (!payload) {
      throw new UnauthorizedError("Sign-in with Google didn't complete. Try again, or use another sign-in method.");
    }

    this.logger.debug('entered email', user.email);
    this.logger.debug('authenticated email', payload?.email);
    const email = payload?.email;
    if (email?.toLowerCase() !== user.email?.toLowerCase()) {
      throw new BadRequestError(
        EMAIL_MISMATCH,
      );
    }
    await UserActivities.create({
      email: email,
      activityType: LOGIN,
      ipAddress: ip,
      loginMode: 'GOOGLE OAUTH',
    });
  }

  async authenticateWithMicrosoft(
    user: Record<string, any>,
    credentials: Record<string, string>,
    ip: string,
  ) {
    const configManagerResponse =
      await this.configurationManagerService.getConfig(
        this.config.cmBackend,
        MICROSOFT_AUTH_CONFIG_PATH,
        user,
        this.config.scopedJwtSecret,
      );
    const { clientId, tenantId } = configManagerResponse.data as MicrosoftSignInConfig;
    const decodedToken = await validateAzureAdUser(credentials, {
      clientId,
      tenantId,
    });
    const identity = microsoftAccountIdentity(decodedToken, tenantId);
    await this.correctEmailFromToken(
      decodedToken,
      user,
      'Microsoft auth',
      identity.emailClaimTrusted,
    );
    if (identity.email !== String(user.email ?? '').toLowerCase()) {
      throw new UnauthorizedError(
        "This Microsoft account doesn't match the account you're signing in to. Sign in with the Microsoft account linked to your PipesHub email.",
      );
    }

    await UserActivities.create({
      email: user.email,
      activityType: LOGIN,
      ipAddress: ip,
      loginMode: 'MICROSOFT OAUTH',
    });
  }

  async authenticate(
    req: AuthSessionRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      this.logger.info('running authenticate');
      const { method, credentials, 'cf-turnstile-response': turnstileToken } = req.body;
      let { sessionInfo } = req;
      let userFindResult;
      let user: Record<string, any> | undefined;
      let userDetails: { firstName?: string; lastName?: string; fullName: string } | undefined;

      if (!method) throw new BadRequestError('method is required');
      if (!sessionInfo) throw new NotFoundError(SIGN_IN_SESSION_EXPIRED);

      if (sessionInfo && !sessionInfo.email) {
        sessionInfo.email = req.body.email || "";
      }

      // A sign-in started before the upgrade may still list a removed method.
      sessionInfo.authConfig = withoutRemovedSignInMethods(sessionInfo.authConfig).steps;
      assertMethodAllowedAtStep(
        sessionInfo.authConfig as IOrgAuthConfigLike['authSteps'] | undefined,
        Number(sessionInfo.currentStep),
        String(method),
      );

      // 1. Password Guard (Turnstile)
      if (method === AuthMethodType.PASSWORD) {
        const turnstileSecretKey = process.env.TURNSTILE_SECRET_KEY;
        if (turnstileSecretKey) {
          const isValid = await verifyTurnstileToken(turnstileToken, turnstileSecretKey, req.ip, this.logger);
          if (!isValid) throw new UnauthorizedError('Invalid CAPTCHA verification. Please try again.');
        }
      }

      const orgId = sessionInfo.orgId;
      const jitConfig = sessionInfo?.jitConfig as Record<string, boolean> | undefined;

      // Identify if the method is an external provider following the Google Flow
      const isExternalProvider = [
        AuthMethodType.GOOGLE,
        AuthMethodType.MICROSOFT,
      ].includes(method as AuthMethodType);

      // 2. EXTERNAL PROVIDER FLOW (Extract Email -> Check JIT -> Provision/Verify)
      if (isExternalProvider) {
        const newUserMock = { orgId, email: sessionInfo?.email };
        let providerEmail: string | undefined;

        switch (method) {
          case AuthMethodType.GOOGLE: {
            const configManagerResponse = await this.configurationManagerService.getConfig(
              this.config.cmBackend, GOOGLE_AUTH_CONFIG_PATH, newUserMock, this.config.scopedJwtSecret
            );
            const { clientId } = configManagerResponse.data;
            const client = new OAuth2Client(clientId);
            const ticket = await client.verifyIdToken({
              idToken: credentials.credential || credentials,
              audience: clientId,
            });
            const payload = ticket.getPayload();
            if (!payload?.email) throw new UnauthorizedError(PROVIDER_SHARED_NO_EMAIL);
            providerEmail = payload.email;
            userDetails = this.jitProvisioningService.extractGoogleUserDetails(payload, providerEmail);
            break;
          }

          case AuthMethodType.MICROSOFT: {
            const configManagerResponse = await this.configurationManagerService.getConfig(
              this.config.cmBackend, MICROSOFT_AUTH_CONFIG_PATH, newUserMock, this.config.scopedJwtSecret
            );
            const { clientId, tenantId } = configManagerResponse.data as MicrosoftSignInConfig;
            const decodedToken = await validateAzureAdUser(credentials, {
              clientId,
              tenantId,
            });
            const identity = microsoftAccountIdentity(decodedToken, tenantId);
            providerEmail = identity.email;
            if (!providerEmail) {
              throw new UnauthorizedError(PROVIDER_SHARED_NO_EMAIL);
            }
            sessionInfo.email = providerEmail;
            userDetails = this.jitProvisioningService.extractMicrosoftUserDetails(decodedToken, providerEmail);
            break;
          }

        }

        if (providerEmail) {
          sessionInfo.email = providerEmail;
          const authToken = iamJwtGenerator(providerEmail, this.config.scopedJwtSecret);
          userFindResult = await this.iamService.getUserByEmail(providerEmail, authToken);
          user = userFindResult?.statusCode === 200 ? userFindResult?.data : null;
          this.assertSameAccountAsEarlierSteps(sessionInfo, user);

          const methodKey = method === AuthMethodType.MICROSOFT ? 'microsoft' : 'google';

          if (!user) {
            if (jitConfig && jitConfig[methodKey] && userDetails) {
              user = await this.jitProvisioningService.provisionUser(
                providerEmail, userDetails, orgId!, methodKey
              );
              await UserActivities.create({
                email: providerEmail, activityType: LOGIN, ipAddress: req.ip,
                loginMode: method === AuthMethodType.MICROSOFT ? 'MICROSOFT OAUTH' : 'GOOGLE OAUTH',
              });
            } else {
              throw new BadRequestError("Account not found. Please contact your administrator.");
            }
          }
        }
      }

      // 3. FINAL CREDENTIAL VERIFICATION
      if (!user) {
        const authToken = iamJwtGenerator(sessionInfo.email || "", this.config.scopedJwtSecret);
        userFindResult = await this.iamService.getUserByEmail(sessionInfo.email || "", authToken);
        user =
          userFindResult?.statusCode === 200 ? userFindResult.data : undefined;
        if (!user) {
          // An unknown email gets the same refusal, after the same hash
          // comparison, as a real account given a wrong password or code.
          const submitted = (credentials ?? {}) as {
            password?: unknown;
            otp?: unknown;
          };
          if (method === AuthMethodType.OTP) {
            await compareWithDecoyHash(submitted.otp);
            throw new UnauthorizedError(WRONG_SIGN_IN_CODE);
          }
          await compareWithDecoyHash(submitted.password);
          throw new BadRequestError(WRONG_EMAIL_OR_PASSWORD);
        }
      }
      this.assertSameAccountAsEarlierSteps(sessionInfo, user);

      switch (method) {
        case AuthMethodType.PASSWORD:
          await this.authenticateWithPassword(user, credentials.password, req.ip!);
          break;
        case AuthMethodType.OTP:
          await this.authenticateWithOtp(user, credentials.otp, req.ip!);
          break;
        case AuthMethodType.GOOGLE:
          await this.authenticateWithGoogle(user, credentials, req.ip!);
          break;
        case AuthMethodType.MICROSOFT:
          await this.authenticateWithMicrosoft(user, credentials, req.ip!);
          break;
        default:
          throw new BadRequestError('Unsupported authentication method');
      }

      // 4. MULTI-STEP HANDLING
      if (sessionInfo.currentStep < sessionInfo.authConfig.length - 1) {
        sessionInfo.userId = String(user._id);
        sessionInfo.currentStep++;
        await this.sessionService.updateSession(sessionInfo);

        const allowedMethods = sessionInfo.authConfig[sessionInfo.currentStep]?.allowedMethods.map((m: any) => m.type) || [];
        const authProviders: Record<string, any> = {};

        if (allowedMethods.includes(AuthMethodType.GOOGLE)) {
          const cfg = await this.configurationManagerService.getConfig(this.config.cmBackend, GOOGLE_AUTH_CONFIG_PATH, user, this.config.scopedJwtSecret);
          authProviders.google = cfg.data;
        }
        if (allowedMethods.includes(AuthMethodType.MICROSOFT)) {
          const cfg = await this.configurationManagerService.getConfig(this.config.cmBackend, MICROSOFT_AUTH_CONFIG_PATH, user, this.config.scopedJwtSecret);
          authProviders.microsoft = cfg.data;
        }

        res.json({
          status: 'success',
          nextStep: sessionInfo.currentStep,
          allowedMethods,
          authProviders,
        });
      } else {
        // 5. FINAL SUCCESS
        await this.sessionService.completeAuthentication(sessionInfo);
        const accessToken = await generateAuthToken(user, this.config.jwtSecret);

        const isFirstLogin = !user.hasLoggedIn;
        recordEvent('login', {
          orgId: user.orgId?.toString(),
          userId: user._id?.toString(),
          email: user.email,
          domain: domainFromEmail(user.email),
          first_login: isFirstLogin,
          auth_method: method,
        });

        if (isFirstLogin) {
          await this.iamService.updateUser(user._id, { hasLoggedIn: true, email: user.email }, accessToken);
        }

        res.status(200).json({
          message: 'Fully authenticated',
          accessToken,
          refreshToken: refreshTokenJwtGenerator(user._id, user.orgId, this.config.scopedJwtSecret),
        });
      }
    } catch (error) {
      next(error);
    }
  }

  userAccountSetup = async (
    req: AuthSessionRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { fullName, password } = req.body;
      const { email } = req.body;
      if (!fullName) {
        throw new BadRequestError('Full Name is required');
      }
      if (!password) {
        throw new BadRequestError('Password is required');
      }
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;

      // Todo: check if password and user full name is already with token

      await this.updatePassword(userId, orgId, password, req.ip || '');

      const { firstName, lastName, designation } = req.body;
      const updateUserResult = await this.iamService.updateUser(
        userId,
        {
          email,
          firstName,
          lastName,
          designation,
          fullName,
        },
        jwt.sign({ userId, orgId }, this.config.jwtSecret, {
          expiresIn: '24h',
        }),
      );

      if (updateUserResult.statusCode !== 200) {
        throw new InternalServerError("We couldn't save your account details. Please try again.");
      }
      const updatedUser = updateUserResult.data;

      recordEvent('signup', {
        orgId: orgId?.toString(),
        userId: userId?.toString(),
        email,
        domain: domainFromEmail(email),
      });

      res.status(200).json(updatedUser);
      return;
    } catch (error) {
      next(error);
    }
  };

  async validateEmailChange(req: AuthenticatedServiceRequest, res: Response, next: NextFunction): Promise<void> {
    try {

      const userId = req?.tokenPayload?.userId;
      const newEmail = req?.tokenPayload?.newEmail;
      const orgId = req?.tokenPayload?.orgId;

      const email = String(newEmail).toLowerCase().trim();
      // This account excluded: opening the link again repeats the write and
      // the event, which is how a failed event write is retried.
      const exists = await Users.findOne({ email, _id: { $ne: userId } });
      if (exists) {
        throw new BadRequestError(`Email already in use: ${newEmail}`);
      }
      const user = await Users.findByIdAndUpdate(
        userId,
        { email },
        { returnDocument: 'after' },
      );
      if (!user) {
        throw new NotFoundError('User not found');
      }

      await UserActivities.create({
        orgId: orgId,
        userId: userId,
        activityType: PASSWORD_CHANGED,
        ipAddress: req.ip || '',
      });

      // A conflict means another login owns the address in the graph, so the
      // event must not write it onto this one. Any other graph failure still
      // publishes, so the event can carry the address, and then fails the
      // request so the link can be opened again.
      let graphFailure: Error | undefined;
      if (userId && orgId) {
        try {
          await this.syncVerifiedEmailToGraph(
            String(userId),
            String(orgId),
            email,
          );
        } catch (syncError) {
          if (syncError instanceof ConflictError) {
            throw syncError;
          }
          graphFailure =
            syncError instanceof Error ? syncError : new Error(String(syncError));
        }
      }

      await this.publishEmailChanged(user);
      if (graphFailure) {
        throw graphFailure;
      }

      res.status(200).json({ message: 'Email updated successfully' });


    } catch (err) {

      next(err);
    }
  }

  /**
   * The graph copies a user's email from this event; without it the old
   * address stays there. A failed write throws, so the request fails and the
   * link can be opened again.
   */
  protected async publishEmailChanged(
    user: InstanceType<typeof Users>,
  ): Promise<void> {
    const event: Event = {
      eventType: EventType.UpdateUserEvent,
      timestamp: Date.now(),
      payload: {
        orgId: user.orgId.toString(),
        userId: user._id.toString(),
        fullName: user.fullName,
        ...(user.firstName && { firstName: user.firstName }),
        ...(user.lastName && { lastName: user.lastName }),
        ...(user.designation && { designation: user.designation }),
        email: user.email,
      } as UserUpdatedEvent,
    };
    await this.eventService.start();
    await this.eventService.publishEvent(event);
    await this.eventService.stop();
  }

  protected async syncVerifiedEmailToGraph(
    userId: string,
    orgId: string,
    email: string,
  ): Promise<void> {
    const token = entityUserWriteJwtGenerator(
      userId,
      orgId,
      this.config.scopedJwtSecret,
    );
    const response = await executeConnectorCommand(
      `${this.config.connectorBackend}/api/v1/entity/user/email`,
      HttpMethod.PATCH,
      { Authorization: `Bearer ${token}` },
      { email },
    );
    if (response?.statusCode === 404) {
      this.logger.warn('Graph user not found while syncing verified email', {
        userId,
      });
      return;
    }
    const statusCode = response?.statusCode;
    if (!statusCode || statusCode < 200 || statusCode >= 300) {
      throw handleBackendError(response, 'sync verified email to graph');
    }
  }

}
