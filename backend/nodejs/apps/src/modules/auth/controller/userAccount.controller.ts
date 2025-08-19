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
} from '../services/cm.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { Org } from '../../user_management/schema/org.schema';

const {
  LOGIN,
  LOGOUT,
  OTP_GENERATE,
  WRONG_OTP,
  WRONG_PASSWORD,
  REFRESH_TOKEN,
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
  ) {}
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

    const isMatching = await bcrypt.compare(
      inputOTP,
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
      const { email } = req.body;
      if (!email) {
        throw new BadRequestError('Email is required');
      }
      const authToken = iamJwtGenerator(email, this.config.scopedJwtSecret);
      let result = await this.iamService.getUserByEmail(email, authToken);

      if (result.statusCode !== 200) {
        throw new NotFoundError(result.data);
      }
      const user = result.data;
      // const domain = getDomainFromEmail(email);
      const orgAuthConfig = await OrgAuthConfig.findOne({
        orgId: user.orgId,
        // domain,
        isDeleted: false,
      });

      if (!orgAuthConfig) {
        throw new NotFoundError('Organisation configuration not found');
      }
      const session = await this.sessionService.createSession({
        userId: user._id,
        email: user.email,
        orgId: user.orgId,
        authConfig: orgAuthConfig.authSteps,
        currentStep: 0,
      });
      if (!session) {
        throw new InternalServerError('Failed to create session');
      }

      if (session.token) {
        res.setHeader('x-session-token', session.token);
      }
      const allowedMethods =
        session.authConfig[0]?.allowedMethods.map((m: any) => m.type) || [];

      const authProviders: Record<string, any> = {};

      if (allowedMethods.includes('google')) {
        const configManagerResponse =
          await this.configurationManagerService.getConfig(
            this.config.cmBackend,
            GOOGLE_AUTH_CONFIG_PATH,
            user,
            this.config.scopedJwtSecret,
          );
        authProviders.google = configManagerResponse.data;
      }

      if (allowedMethods.includes('microsoft')) {
        const configManagerResponse =
          await this.configurationManagerService.getConfig(
            this.config.cmBackend,
            MICROSOFT_AUTH_CONFIG_PATH,
            user,
            this.config.scopedJwtSecret,
          );
        authProviders.microsoft = configManagerResponse.data;
      }

      if (allowedMethods.includes(AuthMethodType.AZURE_AD)) {
        const configManagerResponse =
          await this.configurationManagerService.getConfig(
            this.config.cmBackend,
            AZURE_AD_AUTH_CONFIG_PATH,
            user,
            this.config.scopedJwtSecret,
          );
        authProviders.azuread = configManagerResponse.data;
      }

      if (allowedMethods.includes(AuthMethodType.OAUTH)) {
        const configManagerResponse =
          await this.configurationManagerService.getConfig(
            this.config.cmBackend,
            OAUTH_AUTH_CONFIG_PATH,
            user,
            this.config.scopedJwtSecret,
          );
        
        const { clientSecret, tokenEndpoint, userInfoEndpoint, ...publicConfig } = configManagerResponse.data;
        authProviders.oauth = publicConfig;
      }

      res.json({
        currentStep: 0,
        allowedMethods,
        message: 'Authentication initialized',
        authProviders, // Send relevant authentication details to frontend
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
      const { email } = req.body;
      if (!email) {
        throw new BadRequestError('Email is required');
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
      const { newPassword } = req.body;
      const { currentPassword } = req.body;
      if (!currentPassword) {
        throw new BadRequestError('currentPassword is required');
      }
      if (!newPassword) {
        throw new BadRequestError('newPassword is required');
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
      res.status(200).send({ data: 'password reset' });
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
      }, {new: true, upsert: true});

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
        `Password incorrect. Attempts remaining: ${
          5 - userCredentials.wrongCredentialCount
        }`,
      );
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
    otp: Number,
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
    if (email !== user.email) {
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

    const { accessToken, idToken } = credentials;

    if (!accessToken && !idToken) {
      throw new BadRequestError('Access token or ID token is required for OAuth authentication');
    }

    try {
      // Verify token and get user info from OAuth provider
      let userInfo;
      
      if (idToken) {
        // For ID tokens, we need proper JWT verification
        // Since this is a generic OAuth implementation, we'll use the userInfo endpoint approach
        // ID token verification requires provider-specific JWKS endpoints and is complex for generic OAuth
        throw new BadRequestError('ID token verification not supported for generic OAuth. Please use access token flow.');
      } else if (accessToken && userInfoEndpoint) {
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
        throw new BadRequestError('Cannot verify user information: missing user info endpoint or ID token');
      }

      // Verify email matches
      const providerEmail = userInfo.email || userInfo.preferred_username || userInfo.sub;
      if (!providerEmail) {
        throw new BadRequestError('No email found in OAuth provider response');
      }

      this.logger.debug('entered email', user.email);
      this.logger.debug('authenticated email', providerEmail);

      if (providerEmail !== user.email) {
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
      const { method, credentials } = req.body;
      const { sessionInfo } = req;
      if (!method) {
        throw new BadRequestError('method is required');
      }
      if (!sessionInfo) {
        throw new NotFoundError('SessionInfo not found');
      }

      const currentStepConfig = sessionInfo.authConfig[sessionInfo.currentStep];
      this.logger.info('currentStepConfig', currentStepConfig);

      if (
        !currentStepConfig.allowedMethods.find((m: any) => m.type === method)
      ) {
        throw new BadRequestError(
          'Invalid authentication method for this step',
        );
      }
      const authToken = iamJwtGenerator(
        sessionInfo.email,
        this.config.scopedJwtSecret,
      );
      const userFindResult = await this.iamService.getUserByEmail(
        sessionInfo.email,
        authToken,
      );
      if (!userFindResult) {
        throw new NotFoundError('User not found');
      }
      const user = userFindResult.data;

      this.logger.debug('method', method);
      switch (method) {
        case AuthMethodType.PASSWORD:
          await this.authenticateWithPassword(
            user,
            credentials.password,
            req.ip!,
          );
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
          await this.authenticateWithMicrosoft(
            user,
            credentials,
            req.ip || ' ',
          );
          break;
        case AuthMethodType.OAUTH:
          await this.authenticateWithOAuth(user, credentials, req.ip!);
          break;
        case AuthMethodType.SAML_SSO:
          break;
        default:
          throw new BadRequestError('Unsupported authentication method');
      }

      if (sessionInfo.currentStep < sessionInfo.authConfig.length - 1) {
        sessionInfo.currentStep++;
        await this.sessionService.updateSession(sessionInfo);
        const allowedMethods =
          sessionInfo.authConfig[sessionInfo.currentStep]?.allowedMethods.map(
            (m: any) => m.type,
          ) || [];

        this.logger.debug(
          'sessionInfo',
          sessionInfo.authConfig[sessionInfo.currentStep]?.allowedMethods,
        );

        const authProviders: Record<string, any> = {};
        // Include client IDs if corresponding authentication methods exist
        if (allowedMethods.includes(AuthMethodType.GOOGLE)) {
          const configManagerResponse =
            await this.configurationManagerService.getConfig(
              this.config.cmBackend,
              GOOGLE_AUTH_CONFIG_PATH,
              user,
              this.config.scopedJwtSecret,
            );
          authProviders.google = configManagerResponse.data;
        }

        if (allowedMethods.includes(AuthMethodType.MICROSOFT)) {
          const configManagerResponse =
            await this.configurationManagerService.getConfig(
              this.config.cmBackend,
              MICROSOFT_AUTH_CONFIG_PATH,
              user,
              this.config.scopedJwtSecret,
            );
          authProviders.microsoft = configManagerResponse.data;
        }

        if (allowedMethods.includes(AuthMethodType.AZURE_AD)) {
          const configManagerResponse =
            await this.configurationManagerService.getConfig(
              this.config.cmBackend,
              AZURE_AD_AUTH_CONFIG_PATH,
              user,
              this.config.scopedJwtSecret,
            );
          authProviders.azuread = configManagerResponse.data;
        }

        if (allowedMethods.includes(AuthMethodType.OAUTH)) {
          const configManagerResponse =
            await this.configurationManagerService.getConfig(
              this.config.cmBackend,
              OAUTH_AUTH_CONFIG_PATH,
              user,
              this.config.scopedJwtSecret,
            );
          
          const { clientSecret, tokenEndpoint, userInfoEndpoint, ...publicConfig } = configManagerResponse.data;
          authProviders.oauth = publicConfig;
        }
        res.json({
          status: 'success',
          nextStep: sessionInfo.currentStep,
          allowedMethods: sessionInfo.authConfig[
            sessionInfo.currentStep
          ].allowedMethods.map((m: any) => m.type),
          authProviders,
        });
        return;
      } else {
        await this.sessionService.completeAuthentication(sessionInfo);
        const accessToken = await generateAuthToken(
          user,
          this.config.jwtSecret,
        );

        if (!user.hasLoggedIn) {
          const userInfo = {
            ...user,
            hasLoggedIn: true,
          };
          await this.iamService.updateUser(user._id, userInfo, accessToken);
          this.logger.info('user updated');
        }
        res.status(200).json({
          message: 'Fully authenticated',
          accessToken,
          refreshToken: refreshTokenJwtGenerator(
            user._id,
            user.orgId,
            this.config.scopedJwtSecret,
          ),
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
      const { code, email, provider, redirectUri } = req.body;

      if (!code || !email || !provider || !redirectUri) {
        this.logger.warn('OAuth token exchange failed: missing required parameters', { 
          hasCode: !!code, 
          hasEmail: !!email, 
          hasProvider: !!provider, 
          hasRedirectUri: !!redirectUri 
        });
        throw new BadRequestError('Missing required OAuth parameters');
      }

      // Find user to get proper context for configuration access
      const authToken = iamJwtGenerator(email, this.config.scopedJwtSecret);
      const user = await this.iamService.getUserByEmail(email, authToken);

      if (user.statusCode !== 200) {
        throw new NotFoundError('User not found');
      }

      // Get OAuth configuration using configuration manager service
      const configManagerResponse =
        await this.configurationManagerService.getConfig(
          this.config.cmBackend,
          OAUTH_AUTH_CONFIG_PATH,
          user.data,
          this.config.scopedJwtSecret,
        );

      if (!configManagerResponse.data) {
        throw new BadRequestError('OAuth is not configured');
      }

      const oauthConfig = configManagerResponse.data;

      this.logger.debug('OAuth token exchange initiated', {
        provider: oauthConfig.providerName || 'Unknown',
        hasValidConfig: !!(oauthConfig.tokenEndpoint && oauthConfig.clientId && oauthConfig.clientSecret)
      });

      if (!oauthConfig.tokenEndpoint) {
        throw new BadRequestError('OAuth token endpoint not configured');
      }

      // Exchange authorization code for tokens
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
          statusText: tokenResponse.statusText,
          errorBody,
          tokenEndpoint: oauthConfig.tokenEndpoint,
        });
        throw new BadRequestError(`Failed to exchange authorization code for tokens: ${tokenResponse.status} ${tokenResponse.statusText} - ${errorBody}`);
      }

      const tokens = await tokenResponse.json();

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
