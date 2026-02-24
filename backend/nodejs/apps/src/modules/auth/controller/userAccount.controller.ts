import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import { Request, Response, NextFunction } from 'express';

import {
  authJwtGenerator,
  iamJwtGenerator,
  iamUserLookupJwtGenerator,
  jwtGeneratorForForgotPasswordLink,
  mailJwtGenerator,
  refreshTokenJwtGenerator,
} from '../../../libs/utils/createJwt';
import { generateOtp } from '../utils/generateOtp';

import { passwordValidator } from '../utils/passwordValidator';

import {
  AuthMethodType,
  OrgAuthConfig,
} from '../schema/orgAuthConfiguration.schema';
import { userActivitiesType } from '../../../libs/utils/userActivities.utils';
import { UserActivities } from '../schema/userActivities.schema';
import {
  AuthenticatedUserRequest,
  AuthenticatedServiceRequest,
} from '../../../libs/middlewares/types';
import { UserCredentials } from '../schema/userCredentials.schema';

import { AuthSessionRequest } from '../middlewares/types';

import { SessionService } from '../services/session.service';
import mongoose from 'mongoose';
import { OAuth2Client } from 'google-auth-library';
import { validateAzureAdUser } from '../utils/azureAdTokenValidation';
import { IamService } from '../services/iam.service';
import { MailService } from '../services/mail.service';

import {
  BadRequestError,
  ForbiddenError,
  GoneError,
  InternalServerError,
  NotFoundError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { inject, injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { generateAuthToken } from '../utils/generateAuthToken';
import {
  AZURE_AD_AUTH_CONFIG_PATH,
  ConfigurationManagerService,
  GOOGLE_AUTH_CONFIG_PATH,
  MICROSOFT_AUTH_CONFIG_PATH,
  OAUTH_AUTH_CONFIG_PATH,
  SSO_AUTH_CONFIG_PATH,
} from '../services/cm.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { Org } from '../../user_management/schema/org.schema';
import { verifyTurnstileToken } from '../../../libs/utils/turnstile-verification';
import { JitProvisioningService } from '../services/jit-provisioning.service';

const {
  LOGIN,
  LOGOUT,
  OTP_GENERATE,
  WRONG_OTP,
  WRONG_PASSWORD,
  REFRESH_TOKEN,
  PASSWORD_CHANGED,
} = userActivitiesType;
export const SALT_ROUNDS = 10;

@injectable()
export class UserAccountController {
  constructor(
    @inject('AppConfig') private config: AppConfig,
    @inject('IamService') private iamService: IamService,
    @inject('MailService') private mailService: MailService,
    @inject('SessionService') private sessionService: SessionService,
    @inject('ConfigurationManagerService')
    private configurationManagerService: ConfigurationManagerService,
    @inject('Logger') private logger: Logger,
    @inject('JitProvisioningService') private jitProvisioningService: JitProvisioningService,
  ) { }
  async generateHashedOTP() {
    const otp = generateOtp();
    const hashedOTP = await bcrypt.hash(otp, SALT_ROUNDS);

    return { otp, hashedOTP };
  }

  async verifyOTP(
    userId: string,
    orgId: string,
    inputOTP: any,
    email: string,
    ipAddress: string,
  ) {
    let userCredentials = await UserCredentials.findOne({
      userId,
      orgId,
      isDeleted: false,
    });
    if (!userCredentials) {
      throw new BadRequestError('Please request OTP before login');
    }
    if (userCredentials.isBlocked) {
      throw new BadRequestError(
        'Your account has been disabled as you have entered incorrect OTP/Password too many times. Please reach out to your admin or reachout to contact@pipeshub.com',
      );
    }
    if (!userCredentials.otpValidity || !userCredentials.hashedOTP) {
      throw new UnauthorizedError('Invalid OTP. Please try again.');
    }
    if (Date.now() > userCredentials.otpValidity) {
      throw new GoneError('OTP has expired. Please request a new one.');
    }

    // Ensure OTP is a string for bcrypt.compare (bcrypt requires both arguments to be strings)
    const otpString = String(inputOTP);
    const isMatching = await bcrypt.compare(
      otpString,
      userCredentials.hashedOTP,
    );
    this.logger.debug('isMatching', isMatching);
    if (!isMatching) {
      userCredentials = await this.incrementWrongCredentialCount(userId, orgId);
      if (!userCredentials) {
        throw new BadRequestError('Please request OTP before login');
      }
      await UserActivities.create({
        email: email,
        activityType: WRONG_OTP,
        ipAddress: ipAddress,
        loginMode: 'OTP',
      });
      if (userCredentials.wrongCredentialCount >= 5) {
        this.logger.warn('blocked', email);
        userCredentials.isBlocked = true;
        await userCredentials.save();

        const org = await Org.findOne({ _id: orgId, isDeleted: false });

        await this.mailService.sendMail({
          emailTemplateType: 'suspiciousLoginAttempt',
          initiator: {
            jwtAuthToken: mailJwtGenerator(email, this.config.scopedJwtSecret),
          },
          usersMails: [email],
          subject: 'Alert : Suspicious Login Attempt Detected',
          templateData: {
            link: this.config.frontendUrl,
            orgName: org?.shortName || org?.registeredName,
          },
        });
        throw new UnauthorizedError(
          'Too many login attempts. Account Blocked.',
        );
      }
      throw new UnauthorizedError('Invalid OTP. Please try again.');
    } else {
      userCredentials.wrongCredentialCount = 0;
      await userCredentials.save();
    }

    return { statusCode: 200 };
  }

  async verifyPassword(password: string, hashedPassword: string) {
    return bcrypt.compare(password, hashedPassword);
  }

  async incrementWrongCredentialCount(userId: string, orgId: string) {
    const userCredentials = await UserCredentials.findOneAndUpdate(
      { userId, orgId, isDeleted: false },
      { $inc: { wrongCredentialCount: 1 } },
      { new: true },
    );

    return userCredentials;
  }
  async hasPasswordMethod(
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.tokenPayload?.orgId;
      const isPasswordAuthEnabled = !!(await OrgAuthConfig.exists({
        orgId,
        authSteps: {
          $elemMatch: {
            allowedMethods: { $elemMatch: { type: 'password' } },
          },
        },
      }));
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

      // The raw list of types allowed in the DB (e.g., ["otp", "google"])
      const dbMethods = orgAuthConfig?.authSteps[0]?.allowedMethods.map((m: any) => m.type) || [];

      const allowedMethods: string[] = [];      // Methods to show on frontend
      const jitEnabledMethods: string[] = [];   // Only methods with JIT actually ON
      const authProviders: Record<string, any> = {};
      const jitConfig: Record<string, boolean> = {};

      const configMethodMap: Record<string, { path: string, key: string }> = {
        'google': { path: GOOGLE_AUTH_CONFIG_PATH, key: 'google' },
        'microsoft': { path: MICROSOFT_AUTH_CONFIG_PATH, key: 'microsoft' },
        [AuthMethodType.AZURE_AD]: { path: AZURE_AD_AUTH_CONFIG_PATH, key: 'azuread' },
        [AuthMethodType.OAUTH]: { path: OAUTH_AUTH_CONFIG_PATH, key: 'oauth' },
        [AuthMethodType.SAML_SSO]: { path: SSO_AUTH_CONFIG_PATH, key: 'saml' },
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
            authProviders[mapping.key === 'azuread' ? 'azuread' : mapping.key] = configData;

            // Only add to JIT lists if enableJit is strictly true
            if (configData?.enableJit === true) {
              jitEnabledMethods.push(method);
              jitConfig[mapping.key] = true;

              if (method === AuthMethodType.OAUTH) {
                const { clientSecret, tokenEndpoint, userInfoEndpoint, ...publicConfig } = configData;
                authProviders.oauth = publicConfig;
              }
            }
          } catch (e) {
            this.logger.debug(`${method} config fetch failed, not adding to allowed methods`);
          }

          if (allowedMethods.includes(AuthMethodType.SAML_SSO)) {
            try {
              const configManagerResponse = await this.configurationManagerService.getConfig(
                this.config.cmBackend,
                SSO_AUTH_CONFIG_PATH,
                newUser,
                this.config.scopedJwtSecret,
              );
              if (configManagerResponse.data?.enableJit) {
                jitEnabledMethods.push(AuthMethodType.SAML_SSO);
                jitConfig.saml = true;
                authProviders.saml = configManagerResponse.data;
              }
            } catch (e) {
              this.logger.debug('SAML config not available for JIT');
            }
          }
        }
      }

      // Final check: if no methods from DB were valid/found, fallback to password
      const finalMethods = allowedMethods.length > 0 ? allowedMethods : ['password'];

      const session = await this.sessionService.createSession({
        userId: 'NOT_FOUND',
        email: email,
        orgId: orgAuthConfig ? orgAuthConfig.orgId.toString() : '',
        authConfig: orgAuthConfig?.authSteps ?? [{ order: 1, allowedMethods: [{ type: 'password' }] }],
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
        initiator: { jwtAuthToken: mailAuthToken },
        usersMails: [user.email],
        subject: 'PipesHub | Reset your password!',
        templateData: {
          orgName: org?.shortName || org?.registeredName,
          name: user.fullName,
          link: resetPasswordLink,
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
  ) {
    try {
      const isPasswordValid = passwordValidator(newPassword);
      if (!isPasswordValid) {
        throw new BadRequestError(
          'Password should have minimum 8 characters with at least one uppercase, one lowercase, one number, and one special character.',
        );
      }
      let userCredentialData = await UserCredentials.findOne({
        userId: userId,
        orgId: orgId,
        isDeleted: false,
      });

      if (userCredentialData?.isBlocked) {
        throw new BadRequestError(
          'You cannot change you password as your account is blocked due to multiple incorrect logins',
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
    try {
      const { email, 'cf-turnstile-response': turnstileToken } = req.body;
      if (!email) {
        throw new BadRequestError('Email is required');
      }

      // Verify Turnstile token
      const turnstileSecretKey = process.env.TURNSTILE_SECRET_KEY;
      if (turnstileSecretKey) { // Only verify if secret key is configured
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

      const authToken = iamJwtGenerator(email, this.config.scopedJwtSecret);
      const user = await this.iamService.getUserByEmail(email, authToken);

      if (user.statusCode !== 200) {
        throw new BadRequestError(user.data);
      }
      this.logger.debug('user', user);

      const result = await this.sendForgotPasswordEmail(user.data);
      if (result.statusCode !== 200) {
        throw new BadRequestError(result.data!);
      }
      res.status(200).send({ data: 'password reset mail sent' });
      return;
    } catch (error) {
      next(error);
    }
  };
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
            allowedMethods: [{ type: 'password', samlConfig: undefined }],
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
        authJwtGenerator(this.config.jwtSecret, null, userId, orgId),
      );

      if (adminCheckResult.statusCode !== 200) {
        throw new NotFoundError(adminCheckResult.data);
      }

      if (!orgId) {
        throw new BadRequestError('OrgId are required');
      }

      // Fetch organization's authentication config
      const orgAuthConfig = await OrgAuthConfig.findOne({ orgId });

      if (!orgAuthConfig) {
        throw new NotFoundError('Organisation config not found');
      }
      const authMethod = orgAuthConfig.authSteps;

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
        authJwtGenerator(this.config.jwtSecret, null, userId, orgId),
      );

      if (adminCheckResult.statusCode !== 200) {
        throw new NotFoundError(adminCheckResult.data);
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
      const userFindResult = await this.iamService.getUserById(
        userId,
        iamUserLookupJwtGenerator(userId, orgId, this.config.scopedJwtSecret),
      );

      if (userFindResult.statusCode !== 200) {
        throw new NotFoundError(userFindResult.data);
      }
      await this.updatePassword(userId, orgId, password, req.ip!);

      res.status(200).send({ data: 'password reset' });
      return;
    } catch (error) {
      next(error);
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
        throw new NotFoundError(userFindResult.data);
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

    if (userCredentialData?.isBlocked) {
      throw new ForbiddenError(
        'OTP not sent. You have entered incorrect OTP/Password too many times. Your account has been disabled. Please reach out to your admin or reachout to contact@pipeshub.com to get it restored.',
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
    try {
      const result = await this.mailService.sendMail({
        emailTemplateType: 'loginWithOTP',
        initiator: {
          jwtAuthToken: mailJwtGenerator(email, this.config.scopedJwtSecret),
        },

        usersMails: [email],
        subject: 'OTP for Login',
        templateData: {
          name: userFullName,
          orgName: org?.shortName || org?.registeredName,
          otp: otp,
        },
      });
      if (result.statusCode !== 200) {
        throw new Error(result.data);
      }
      return { statusCode: 200, data: 'OTP sent' };
    } catch (err) {
      throw err;
    }
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
      let result = await this.iamService.getUserByEmail(email, authToken);
      if (result.statusCode !== 200) {
        throw new NotFoundError(result.data);
      }
      const user = result.data;

      result = await this.generateAndSendLoginOtp(
        user._id,
        user.orgId,
        user.fullName,
        email,
        req.ip || ' ',
      );

      if (result.statusCode !== 200) {
        throw new BadRequestError(result.data);
      }
      res.status(200).send(result.data);
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

      await UserActivities.create({
        orgId,
        userId,
        activityType: REFRESH_TOKEN,
        ipAddress: req.ip,
      });

      const result = await this.iamService.getUserById(
        userId,
        iamUserLookupJwtGenerator(userId, orgId, this.config.scopedJwtSecret),
      );
      if (result.statusCode !== 200) {
        throw new NotFoundError(result.data);
      }

      const user = result.data;

      if (!user) {
        throw new NotFoundError('User not found');
      }

      const userCredential = await UserCredentials.findOneAndUpdate({
        userId: userId,
        orgId: orgId,
        isDeleted: false,
      }, {
        $set: {
          lastLogin: Date.now(),
          ipAddress: req.ip,
        },
      }, { new: true, upsert: true });

      if (!userCredential) {
        throw new NotFoundError('User credentials not found');
      }

      if (userCredential.isBlocked) {
        throw new BadRequestError(
          'Your account has been disabled. If it is a mistake, Please reach out to contact@pipeshub.com to get it restored.',
        );
      }

      const accessToken = await generateAuthToken(user, this.config.jwtSecret);

      res.status(200).json({ user: user, accessToken: accessToken });
      return;
    } catch (error) {
      next(error);
    }
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
    password: string,
    ip: string,
  ) {
    const userId = user._id;
    const orgId = user.orgId;
    const email = user.email;
    const org = await Org.findOne({ _id: user.orgId, isDeleted: false });

    let userCredentials = await UserCredentials.findOne({
      orgId,
      userId,
      isDeleted: false,
    });

    if (!userCredentials?.hashedPassword) {
      throw new NotFoundError(
        'You have not created a password yet. Please create a new password by using forgot password',
      );
    }
    if (userCredentials.isBlocked) {
      throw new BadRequestError(
        'Your account has been disabled as you have entered incorrect OTP/Password too many times. Please reach out to us to get it restored.',
      );
    }

    const isPasswordCorrect = await this.verifyPassword(
      password,
      userCredentials.hashedPassword,
    );

    if (!isPasswordCorrect) {
      userCredentials = await this.incrementWrongCredentialCount(userId, orgId);
      if (!userCredentials) {
        throw new BadRequestError('Please request OTP before login');
      }
      await UserActivities.create({
        email: email,
        activityType: WRONG_PASSWORD,
        ipAddress: ip,
        loginMode: 'OTP',
      });
      if (userCredentials.wrongCredentialCount >= 5) {
        userCredentials.isBlocked = true;
        await userCredentials.save();

        await this.mailService.sendMail({
          emailTemplateType: 'suspiciousLoginAttempt',
          initiator: {
            jwtAuthToken: mailJwtGenerator(email, this.config.scopedJwtSecret),
          },
          usersMails: [email],
          subject: 'Alert : Suspicious Login Attempt Detected',
          templateData: {
            link: this.config.frontendUrl,
            orgName: org?.shortName || org?.registeredName,
          },
        });
      }
      throw new BadRequestError(
        "Incorrect password, please try again."
      )
    } else {
      userCredentials.wrongCredentialCount = 0;
      await userCredentials.save();
    }

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
      throw new BadRequestError('Error verifying OTP');
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
      throw new UnauthorizedError('Error authorizing user through google');
    }

    this.logger.debug('entered email', user.email);
    this.logger.debug('authenticated email', payload?.email);
    const email = payload?.email;
    if (email?.toLowerCase() !== user.email?.toLowerCase()) {
      throw new BadRequestError(
        'Email mismatch: Token email does not match session email.',
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
    const { tenantId } = configManagerResponse.data;

    await validateAzureAdUser(credentials, tenantId);

    await UserActivities.create({
      email: user.email,
      activityType: LOGIN,
      ipAddress: ip,
      loginMode: 'MICROSOFT OAUTH',
    });
  }

  async authenticateWithAzureAd(
    user: Record<string, any>,
    credentials: Record<string, string>,
    ip: string,
  ) {
    const configManagerResponse =
      await this.configurationManagerService.getConfig(
        this.config.cmBackend,
        AZURE_AD_AUTH_CONFIG_PATH,
        user,
        this.config.scopedJwtSecret,
      );
    const { tenantId } = configManagerResponse.data;
    await validateAzureAdUser(credentials, tenantId);

    await UserActivities.create({
      email: user.email,
      activityType: LOGIN,
      ipAddress: ip,
      loginMode: 'AZUREAD OAUTH',
    });
  }

  async authenticateWithOAuth(
    user: Record<string, any>,
    credentials: Record<string, any>,
    ip: string,
  ) {
    const configManagerResponse =
      await this.configurationManagerService.getConfig(
        this.config.cmBackend,
        OAUTH_AUTH_CONFIG_PATH,
        user,
        this.config.scopedJwtSecret,
      );

    const {
      userInfoEndpoint
    } = configManagerResponse.data;
    const { accessToken } = credentials;

    if (!accessToken) {
      throw new BadRequestError('Access token is required for OAuth authentication');
    }

    if (!userInfoEndpoint) {
      throw new BadRequestError('User info endpoint is required for OAuth authentication');
    }

    try {
      // Verify token and get user info from OAuth provider
      let userInfo;

      if (accessToken && userInfoEndpoint) {
        // If access token is provided, fetch user info from the provider
        const userInfoResponse = await fetch(userInfoEndpoint, {
          headers: {
            'Authorization': `Bearer ${accessToken}`,
            'Content-Type': 'application/json',
          },
        });

        if (!userInfoResponse.ok) {
          this.logger.warn('OAuth userinfo fetch failed', {
            status: userInfoResponse.status,
            provider: configManagerResponse.data.providerName
          });
          throw new UnauthorizedError('Failed to fetch user information from OAuth provider');
        }

        userInfo = await userInfoResponse.json();
      } else {
        throw new BadRequestError('Cannot verify user information: missing user info endpoint or access token');
      }

      // Verify email matches
      const providerEmail = userInfo.email || userInfo.preferred_username || userInfo.user_id;
      if (!providerEmail) {
        throw new BadRequestError('No email found in OAuth provider response');
      }

      this.logger.debug('entered email', user.email);
      this.logger.debug('authenticated email', providerEmail);

      if (providerEmail?.toLowerCase() !== user.email?.toLowerCase()) {
        throw new BadRequestError(
          'Email mismatch: OAuth provider email does not match session email.',
        );
      }

      await UserActivities.create({
        email: user.email,
        activityType: LOGIN,
        ipAddress: ip,
        loginMode: 'OAUTH',
      });

    } catch (error) {
      if (error instanceof Error && (error.message.includes('BadRequestError') || error.message.includes('UnauthorizedError'))) {
        throw error;
      }
      throw new UnauthorizedError(`OAuth authentication failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
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
      if (!sessionInfo) throw new NotFoundError('SessionInfo not found');

      if (sessionInfo && !sessionInfo.email) {
        sessionInfo.email = req.body.email || "";
      }

      // 1. Password Guard (Turnstile)
      if (method === AuthMethodType.PASSWORD) {
        const turnstileSecretKey = process.env.TURNSTILE_SECRET_KEY;
        if (turnstileSecretKey) {
          const isValid = await verifyTurnstileToken(turnstileToken, turnstileSecretKey, req.ip, this.logger);
          if (!isValid) throw new UnauthorizedError('Invalid CAPTCHA verification. Please try again.');
        }
      }

      // SAML_SSO follows a different flow - handling it early as per original code
      if (method === AuthMethodType.SAML_SSO) {
        return;
      }

      const orgId = sessionInfo.orgId;
      const jitConfig = sessionInfo?.jitConfig as Record<string, boolean> | undefined;

      // Identify if the method is an external provider following the Google Flow
      const isExternalProvider = [
        AuthMethodType.GOOGLE,
        AuthMethodType.MICROSOFT,
        AuthMethodType.AZURE_AD,
        AuthMethodType.OAUTH
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
            if (!payload?.email) throw new UnauthorizedError('Email not found in Google token');
            providerEmail = payload.email;
            userDetails = this.jitProvisioningService.extractGoogleUserDetails(payload, providerEmail);
            break;
          }

          case AuthMethodType.MICROSOFT:
          case AuthMethodType.AZURE_AD: {
            const configPath = method === AuthMethodType.MICROSOFT ? MICROSOFT_AUTH_CONFIG_PATH : AZURE_AD_AUTH_CONFIG_PATH;
            const configManagerResponse = await this.configurationManagerService.getConfig(
              this.config.cmBackend, configPath, newUserMock, this.config.scopedJwtSecret
            );
            const { tenantId } = configManagerResponse.data;
            const decodedToken = await validateAzureAdUser(credentials, tenantId);
            providerEmail = decodedToken.email || decodedToken.upn || decodedToken.preferred_username;
            userDetails = this.jitProvisioningService.extractMicrosoftUserDetails(decodedToken, providerEmail!);
            break;
          }

          case AuthMethodType.OAUTH: {
            const configManagerResponse = await this.configurationManagerService.getConfig(
              this.config.cmBackend, OAUTH_AUTH_CONFIG_PATH, newUserMock, this.config.scopedJwtSecret
            );
            const { userInfoEndpoint } = configManagerResponse.data;
            const { accessToken } = credentials;
            if (!accessToken) throw new BadRequestError('Access token is required');

            const userInfoResponse = await fetch(userInfoEndpoint, {
              headers: { 'Authorization': `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
            });
            if (!userInfoResponse.ok) throw new UnauthorizedError('Failed to fetch user info');
            const userInfo = await userInfoResponse.json();
            providerEmail = userInfo.email || userInfo.preferred_username || userInfo.sub;

            if (!providerEmail) {
              throw new BadRequestError('Email mismatch: OAuth provider email does not match session email.');
            }
            userDetails = this.jitProvisioningService.extractOAuthUserDetails(userInfo, providerEmail!);
            break;
          }
        }

        if (providerEmail) {
          sessionInfo.email = providerEmail;
          const authToken = iamJwtGenerator(providerEmail, this.config.scopedJwtSecret);
          userFindResult = await this.iamService.getUserByEmail(providerEmail, authToken);
          user = userFindResult?.data;

          const methodKey = method === AuthMethodType.AZURE_AD ? 'azureAd' :
            method === AuthMethodType.MICROSOFT ? 'microsoft' :
              method === AuthMethodType.GOOGLE ? 'google' : 'oauth';

          if (user?.message === "Account not found") {
            if (jitConfig && jitConfig[methodKey] && userDetails) {
              user = await this.jitProvisioningService.provisionUser(
                providerEmail, userDetails, orgId!, methodKey as any
              );
              await UserActivities.create({
                email: providerEmail, activityType: LOGIN, ipAddress: req.ip,
                loginMode: (method === AuthMethodType.AZURE_AD ? 'AZUREAD OAUTH' :
                  method === AuthMethodType.MICROSOFT ? 'MICROSOFT OAUTH' :
                    method === AuthMethodType.GOOGLE ? 'GOOGLE OAUTH' : 'OAUTH'),
              });
            } else {
              throw new BadRequestError("Account not found. Please contact your administrator.");
            }
          }
        }
      }

      // 3. FINAL CREDENTIAL VERIFICATION
      if (!user || user?.message === "Account not found") {
        const authToken = iamJwtGenerator(sessionInfo.email || "", this.config.scopedJwtSecret);
        userFindResult = await this.iamService.getUserByEmail(sessionInfo.email || "", authToken);
        user = userFindResult?.data;
        if (!user) throw new NotFoundError('User not found');
      }

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
        case AuthMethodType.AZURE_AD:
          await this.authenticateWithAzureAd(user, credentials, req.ip!);
          break;
        case AuthMethodType.MICROSOFT:
          await this.authenticateWithMicrosoft(user, credentials, req.ip!);
          break;
        case AuthMethodType.OAUTH:
          await this.authenticateWithOAuth(user, credentials, req.ip!);
          break;
        case AuthMethodType.SAML_SSO:
          break;
        default:
          throw new BadRequestError('Unsupported authentication method');
      }

      // 4. MULTI-STEP HANDLING
      if (sessionInfo.currentStep < sessionInfo.authConfig.length - 1) {
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
        if (allowedMethods.includes(AuthMethodType.AZURE_AD)) {
          const cfg = await this.configurationManagerService.getConfig(this.config.cmBackend, AZURE_AD_AUTH_CONFIG_PATH, user, this.config.scopedJwtSecret);
          authProviders.azuread = cfg.data;
        }
        if (allowedMethods.includes(AuthMethodType.OAUTH)) {
          const cfg = await this.configurationManagerService.getConfig(this.config.cmBackend, OAUTH_AUTH_CONFIG_PATH, user, this.config.scopedJwtSecret);
          const { clientSecret, tokenEndpoint, userInfoEndpoint, ...publicConfig } = cfg.data;
          authProviders.oauth = publicConfig;
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

        if (!user.hasLoggedIn) {
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
        throw new InternalServerError('Error checking admin');
      }
      const updatedUser = updateUserResult.data;

      res.status(200).json(updatedUser);
      return;
    } catch (error) {
      next(error);
    }
  };

  async exchangeOAuthToken(
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { code, provider, redirectUri } = req.body;

      // 1. Initial Validation
      if (!code || !provider || !redirectUri) {
        this.logger.warn('OAuth token exchange failed: missing required parameters');
        throw new BadRequestError('Missing required OAuth parameters');
      }

      // 2. Get bootstrap config to perform the exchange
      // Using the first available org context to get the client credentials
      const initialOrg = await Org.findOne({ isDeleted: false });
      if (!initialOrg) throw new BadRequestError('Organization not found');

      const configResponse = await this.configurationManagerService.getConfig(
        this.config.cmBackend,
        OAUTH_AUTH_CONFIG_PATH,
        { orgId: (initialOrg._id as any).toString() },
        this.config.scopedJwtSecret,
      );

      const oauthConfig = configResponse.data;
      if (!oauthConfig?.tokenEndpoint || !oauthConfig?.clientSecret) {
        throw new BadRequestError('OAuth is not properly configured');
      }

      // 3. Exchange authorization code for tokens (Functionality strictly maintained)
      const tokenResponse = await fetch(oauthConfig.tokenEndpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
          grant_type: 'authorization_code',
          client_id: oauthConfig.clientId,
          client_secret: oauthConfig.clientSecret,
          code,
          redirect_uri: redirectUri,
        }),
      });

      if (!tokenResponse.ok) {
        const errorBody = await tokenResponse.text();
        this.logger.error('OAuth token exchange failed', {
          status: tokenResponse.status,
          errorBody,
        });
        throw new BadRequestError(`Failed to exchange authorization code for tokens from Oauth: ${tokenResponse.status}`);
      }

      const tokens = await tokenResponse.json();

      // 4. Use the access_token to get the user's email for JIT/Existence check
      const userInfoResponse = await fetch(oauthConfig.userInfoEndpoint, {
        headers: {
          'Authorization': `Bearer ${tokens.access_token}`,
          'Content-Type': 'application/json',
        },
      });

      if (!userInfoResponse.ok) {
        throw new UnauthorizedError('Failed to fetch user information from OAuth provider');
      }

      const userInfo = await userInfoResponse.json();
      const providerEmail = userInfo.email || userInfo.preferred_username || userInfo.sub;

      if (!providerEmail) {
        throw new BadRequestError('Email not found in OAuth provider response');
      }

      // 5. Apply the "Google Flow" for user check and JIT
      const authToken = iamJwtGenerator(providerEmail, this.config.scopedJwtSecret);
      const userResult = await this.iamService.getUserByEmail(providerEmail, authToken);
      let user = userResult.statusCode === 200 ? userResult.data : null;

      if (!user || user?.message === "Account not found") {
        // If jit is false and user is Account not found then give badrequest
        if (!oauthConfig.enableJit) {
          throw new NotFoundError('Account not found. Please contact your administrator.');

        }

        // If jit is true and user is Account not found then jitProvision the user
        const userDetails = this.jitProvisioningService.extractOAuthUserDetails(userInfo, providerEmail);
        user = await this.jitProvisioningService.provisionUser(
          providerEmail,
          userDetails,
          (initialOrg._id as any).toString(),
          'oauth'
        );

        // Log activity for new JIT user

      }

      await UserActivities.create({
        email: providerEmail,
        activityType: LOGIN,
        ipAddress: req.ip,
        loginMode: 'OAUTH',
      });


      // 6. FINAL RESPONSE (Strictly maintained keys)
      res.status(200).json({
        access_token: tokens.access_token,
        id_token: tokens.id_token,
        token_type: tokens.token_type,
        expires_in: tokens.expires_in,
      });

    } catch (error) {
      next(error);
    }
  }
}
