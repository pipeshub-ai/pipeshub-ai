import { Response, NextFunction } from 'express';
import mongoose from 'mongoose';
import bcrypt from 'bcryptjs';

import { Org } from '../schema/org.schema';
import { Users } from '../schema/users.schema';
import { UserGroups } from '../schema/userGroup.schema';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { OrgLogos } from '../schema/orgLogo.schema';
import sharp from 'sharp';
import { inject, injectable } from 'inversify';
import { MailService } from '../services/mail.service';
import {
  AuthMethodType,
  OrgAuthConfig,
} from '../../auth/schema/orgAuthConfiguration.schema';
import { UserCredentials } from '../../auth/schema/userCredentials.schema';
import { passwordValidator } from '../../auth/utils/passwordValidator';
import { SALT_ROUNDS } from '../../auth/controller/userAccount.controller';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';
import { ContainerRequest } from '../../auth/middlewares/types';
import {
  EntitiesEventProducer,
  Event,
  EventType,
  OrgAddedEvent,
  OrgDeletedEvent,
  OrgUpdatedEvent,
  UserAddedEvent,
} from '../services/entity_events.service';
import { mailJwtGenerator } from '../../../libs/utils/createJwt';
import { AppConfig } from '../../tokens_manager/config/config';
import { PrometheusService } from '../../../libs/services/prometheus/prometheus.service';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { ORG_CREATED_ACTIVITY } from '../constants/constants';
import { GlobalReaderTeamService } from '../services/globalReaderTeam.service';

@injectable()
export class OrgController {
  constructor(
    @inject('AppConfig') private config: AppConfig,
    @inject('MailService') private mailService: MailService,
    @inject('Logger') private logger: Logger,
    @inject('EntitiesEventProducer')
    private eventService: EntitiesEventProducer,
    @inject('GlobalReaderTeamService')
    private globalReaderTeamService: GlobalReaderTeamService,
  ) {}

  getDomainFromEmail(email: string) {
    const parts = email.split('@');

    if (parts.length !== 2) {
      return null; // Invalid email format
    }

    // The domain is the second part of the split
    const domain = parts[1];

    return domain;
  }

  /**
   * Validates SVG content for dangerous elements and attributes
   * Throws an error if scripts, event handlers, or other dangerous content is found
   * Does not modify the buffer - only validates
   * @param svgBuffer - The SVG file buffer to validate
   * @throws BadRequestError if dangerous content is detected
   */
  private validateSVG(svgBuffer: Buffer): void {
    try {
      // Input length limit to prevent DoS attacks
      const MAX_SVG_SIZE = 10 * 1024 * 1024; // 10MB max
      if (svgBuffer.length > MAX_SVG_SIZE) {
        throw new BadRequestError('SVG file is too large (max 10MB)');
      }

      const svgString = svgBuffer.toString('utf-8');
      
      // Limit string length to prevent ReDoS - only check first portion if too long
      const maxCheckLength = 1000000; // 1MB in characters for validation
      const checkString = svgString.length > maxCheckLength 
        ? svgString.substring(0, maxCheckLength) 
        : svgString;
      
      // Use limited quantifiers [^>]{0,10000} to prevent ReDoS attacks
      // Check for dangerous patterns - throw error if found
      
      // Check for script tags (opening, closing, or incomplete)
      const scriptTagPattern = /<[\s]*script[^>]{0,10000}>/gi;
      const scriptClosingPattern = /<\/[\s]*script[^>]{0,10000}>/gi;
      if (scriptTagPattern.test(checkString) || scriptClosingPattern.test(checkString)) {
        throw new BadRequestError(
          'SVG file contains script tags which are not allowed for security reasons',
        );
      }

      // Check for event handlers (onclick, onload, onerror, etc.)
      const eventHandlerPattern = /\s+on\w+\s*=\s*["'][^"'>]{0,1000}["']?/gi;
      if (eventHandlerPattern.test(checkString)) {
        throw new BadRequestError(
          'SVG file contains event handlers (onclick, onload, etc.) which are not allowed for security reasons',
        );
      }

      // Check for javascript: protocols
      const javascriptProtocolPattern = /javascript:/gi;
      if (javascriptProtocolPattern.test(checkString)) {
        throw new BadRequestError(
          'SVG file contains javascript: protocols which are not allowed for security reasons',
        );
      }

      // Check for data: protocols with HTML/text
      const dataHtmlPattern = /data:\s*text\/html/gi;
      if (dataHtmlPattern.test(checkString)) {
        throw new BadRequestError(
          'SVG file contains data:text/html protocols which are not allowed for security reasons',
        );
      }

      // Check for iframe, object, embed tags
      const iframePattern = /<[\s]*iframe[^>]{0,10000}>/gi;
      const objectPattern = /<[\s]*object[^>]{0,10000}>/gi;
      const embedPattern = /<[\s]*embed[^>]{0,10000}>/gi;
      if (
        iframePattern.test(checkString) ||
        objectPattern.test(checkString) ||
        embedPattern.test(checkString)
      ) {
        throw new BadRequestError(
          'SVG file contains iframe, object, or embed tags which are not allowed for security reasons',
        );
      }

      // Validation passed - buffer is safe and unmodified
    } catch (error) {
      this.logger.error('SVG validation failed', { error });
      if (error instanceof BadRequestError) {
        throw error;
      }
      throw new BadRequestError('Invalid SVG file format');
    }
  }

  async checkOrgExistence(res: Response): Promise<void> {
    const count = await Org.countDocuments();

    res.status(200).json({ exists: count != 0 });
  }

  async createOrg(req: ContainerRequest, res: Response): Promise<void> {
    const container = req.container;
    if (!container) {
      throw new NotFoundError('Container not found');
    }

    const prometheusService =
      container.get<PrometheusService>(PrometheusService);

    let session: mongoose.ClientSession | null = null;
    try {
      const { contactEmail, adminFullName, password, sendEmail } = req.body;

      if (!passwordValidator(password)) {
        throw new BadRequestError(
          'Password should have minimum 8 characters with at least one uppercase, one lowercase, one number and one special character',
        );
      }

      const count = await Org.countDocuments();
      if (count > 0) {
        throw new BadRequestError('There is already an organization');
      }
      const domain = this.getDomainFromEmail(contactEmail);
      if (!domain) {
        throw new BadRequestError(
          'Please specify a correct domain name. e.g. emailname@example.com',
        );
      }

      const org = new Org({
        ...req.body,
        domain,
        onBoardingStatus: 'notConfigured', // Set default onboarding status when org is created
      });

      const adminUser = new Users({
        fullName: adminFullName,
        email: contactEmail,
        orgId: org._id,
      });

      const hashedPassword = await bcrypt.hash(password, SALT_ROUNDS);

      const adminUserCredentials = new UserCredentials({
        userId: adminUser._id,
        orgId: org._id,
        isDeleted: false,
        hashedPassword,
        ipAddress: req.ip,
      });

      const adminUserGroup = new UserGroups({
        type: 'admin',
        name: 'admin',
        orgId: org._id,
        users: [adminUser._id],
      });

      const allUsersGroup = new UserGroups({
        type: 'everyone',
        name: 'everyone',
        orgId: org._id,
        users: [adminUser._id],
      });

      const standardUsersGroup = new UserGroups({
        type: 'standard',
        name: 'standard',
        orgId: org._id,
        users: [],
      });

      if (!org._id) {
        throw new NotFoundError('Org Id not found');
      }
      if (!adminUser._id) {
        throw new NotFoundError('Admin User Id not found');
      }

      const orgAuthConfig = new OrgAuthConfig({
        orgId: org._id,
        authSteps: [
          {
            order: 1,
            allowedMethods: [{ type: AuthMethodType.PASSWORD }],
          },
        ],
      });

      const rsAvailable = this.config.rsAvailable === 'true';
      if (rsAvailable) {
        session = await mongoose.startSession();

        session.startTransaction();
        await orgAuthConfig.save({ session });
        await adminUserGroup.save({ session });
        await allUsersGroup.save({ session });
        await standardUsersGroup.save({ session });
        await adminUser.save({ session });
        await adminUserCredentials.save({ session });
        await org.save({ session });
        await session.commitTransaction();
      } else {
        await orgAuthConfig.save();
        await adminUserGroup.save();
        await allUsersGroup.save();
        await standardUsersGroup.save();
        await adminUser.save();
        await adminUserCredentials.save();
        await org.save();
      }
      prometheusService.recordActivity(
        ORG_CREATED_ACTIVITY,
        adminUser._id?.toString(),
        org._id?.toString(),
        contactEmail,
        adminFullName,
        req.context?.requestId,
        req.method,
        req.path,
        JSON.stringify(req.context),
        HTTP_STATUS.OK,
      );

      if (sendEmail) {
        await this.mailService.sendMail({
          emailTemplateType: 'accountCreation',
          initiator: {
            jwtAuthToken: mailJwtGenerator(
              contactEmail,
              this.config.scopedJwtSecret,
            ),
          },
          usersMails: [contactEmail],
          subject: 'New Org Account Creation',
          templateData: {
            invitee: 'PipesHub',
            name: 'User',
            orgName: org.registeredName,
            link: `${this.config.frontendUrl}`,
          },
        });
      }

      await this.eventService.start();
      let event: Event = {
        eventType: EventType.OrgCreatedEvent,
        timestamp: Date.now(),
        payload: {
          orgId: org._id,
          accountType: org.accountType,
          registeredName: org.registeredName,
        } as OrgAddedEvent,
      };
      await this.eventService.publishEvent(event);

      event = {
        eventType: EventType.NewUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: adminUser.orgId.toString(),
          userId: adminUser._id,
          fullName: adminUser.fullName,
          email: adminUser.email,
          syncAction: 'none',
        } as UserAddedEvent,
      };
      await this.eventService.publishEvent(event);

      await this.eventService.stop();

      // Create Global Reader team for the new organization
      // This is non-blocking - errors are logged but don't fail org creation
      await this.globalReaderTeamService.ensureGlobalReaderTeamExists(
        org._id.toString(),
        adminUser._id.toString(),
        {
          'Content-Type': 'application/json',
        },
      );

      res.status(200).json(org);
    } catch (error) {
      throw new InternalServerError(
        error instanceof Error ? error.message : 'Error retrieving users',
      );
    } finally {
      if (session) {
        session.endSession();
      }
    }
  }

  async getOrganizationById(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const orgId = req.user?.orgId;
    this.logger.info(orgId);
    try {
      const org = await Org.findOne({ orgId, isDeleted: false });

      if (!org) {
        throw new NotFoundError('Organisation not found');
      }
      res.status(200).json(org);
      return;
    } catch (error) {
      next(error);
    }
  }

  async updateOrganizationDetails(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const { contactEmail, registeredName, shortName, permanentAddress } =
      req.body as {
        contactEmail?: string;
        registeredName?: string;
        shortName?: string;
        permanentAddress?: string;
      };

    try {
      const orgId = req.user?.orgId;

      const org = await Org.findOne({ orgId, isDeleted: false });

      if (!org) {
        throw new NotFoundError('Organisation not found');
      }

      // Update only the fields that are provided in the request body
      const updateData: Partial<{
        contactEmail: string;
        registeredName: string;
        shortName: string;
        permanentAddress: string;
      }> = {};

      if (contactEmail) updateData.contactEmail = contactEmail;
      if (registeredName) updateData.registeredName = registeredName;
      if (shortName) updateData.shortName = shortName;
      if (permanentAddress) updateData.permanentAddress = permanentAddress;

      // Perform the update
      const updatedOrg = await Org.findByIdAndUpdate(orgId, updateData, {
        new: true,
      });

      await this.eventService.start();
      let event: Event = {
        eventType: EventType.OrgUpdatedEvent,
        timestamp: Date.now(),
        payload: {
          orgId: org._id,
          registeredName: org.registeredName,
        } as OrgUpdatedEvent,
      };
      await this.eventService.publishEvent(event);

      await this.eventService.stop();

      res.status(200).json({
        message: 'Organization updated successfully',
        data: updatedOrg,
      });
      return;
    } catch (error) {
      next(error);
    }
  }

  async deleteOrganization(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user?.orgId;
      const org = await Org.findOne({ orgId, isDeleted: false });

      if (!org) {
        throw new NotFoundError('Organisation not found');
      }

      // Soft delete: set isDeleted to true
      org.isDeleted = true;
      await org.save();

      await this.eventService.start();
      let event: Event = {
        eventType: EventType.OrgDeletedEvent,
        timestamp: Date.now(),
        payload: {
          orgId: org._id,
        } as OrgDeletedEvent,
      };
      await this.eventService.publishEvent(event);

      await this.eventService.stop();

      res.status(200).json({
        message: 'Organization marked as deleted successfully',
        data: org,
      });
      return;
    } catch (error) {
      next(error);
    }
  }

  async updateOrgLogo(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const logoFile = req.body.fileBuffer;
      const orgId = req.user?.orgId;
      if (!logoFile) {
        throw new BadRequestError('Organisation logo file is required');
      }

      const isSvg = logoFile.mimetype === 'image/svg+xml';
      let processedBuffer: Buffer;
      let mimeType: string;

      if (isSvg) {
        // For SVG files, validate for dangerous content and reject if found
        // This preserves the original buffer without corruption
        this.validateSVG(logoFile.buffer);
        // Use original buffer if validation passes - no modification
        processedBuffer = logoFile.buffer;
        // Always set mimeType to a safe, hardcoded value (not user-controlled)
        mimeType = 'image/svg+xml';
      } else {
        // For raster images (PNG, JPEG, etc.), convert to JPEG for compression
        const MAX_LOGO_SIZE_BYTES = 100 * 1024;
        const INITIAL_JPEG_QUALITY = 100;
        const MIN_JPEG_QUALITY = 10;
        const JPEG_QUALITY_STEP = 10;

        let quality = INITIAL_JPEG_QUALITY + JPEG_QUALITY_STEP;

        do {
          quality -= JPEG_QUALITY_STEP;
          processedBuffer = await sharp(logoFile.buffer)
            .jpeg({ quality })
            .toBuffer();
        } while (processedBuffer.length > MAX_LOGO_SIZE_BYTES && quality > MIN_JPEG_QUALITY);

        mimeType = 'image/jpeg';
      }

      const base64Logo = processedBuffer.toString('base64');
      await OrgLogos.findOneAndUpdate(
        {
          orgId,
        },
        { orgId, logo: base64Logo, mimeType },
        { new: true, upsert: true },
      );

      // Return JSON response instead of raw buffer - prevents XSS vulnerability
      // The frontend doesn't use the response and fetches the logo separately via getOrgLogo()
      res.status(201).json({
        message: 'Logo updated successfully',
        mimeType: mimeType,
      });
      return;
    } catch (error) {
      next(error);
    }
  }

  async getOrgLogo(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user?.orgId;

      const orgLogo = await OrgLogos.findOne({ orgId }).lean().exec();

      if (!orgLogo || !orgLogo.logo) {
        res.status(204).end();
        return;
      }

      const logoBuffer = Buffer.from(orgLogo.logo, 'base64');
      if (orgLogo.mimeType) {
        res.setHeader('Content-Type', orgLogo.mimeType);
      }

      res.status(200).send(logoBuffer);
      return;
    } catch (error) {
      next(error);
    }
  }

  async removeOrgLogo(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user?.orgId;
      const orgLogo = await OrgLogos.findOne({ orgId }).exec();
      if (!orgLogo) {
        throw new NotFoundError('Organisation logo not found');
      }

      orgLogo.logo = null;
      orgLogo.mimeType = null;

      await orgLogo.save();

      res.status(200).send(orgLogo);
      return;
    } catch (error) {
      next(error);
    }
  }

  async getOnboardingStatus(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user?.orgId;

      const org = await Org.findOne({ _id: orgId, isDeleted: false })
        .select('onBoardingStatus')
        .lean();

      if (!org) {
        throw new NotFoundError('Organisation not found');
      }

      res.status(200).json({
        status: org.onBoardingStatus || 'notConfigured',
      });
      return;
    } catch (error) {
      next(error);
    }
  }

  async updateOnboardingStatus(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { status } = req.body;
      const orgId = req.user?.orgId;

      // Validate status value
      if (!['configured', 'notConfigured', 'skipped'].includes(status)) {
        throw new BadRequestError(
          'Invalid onboarding status. Must be one of: configured, notConfigured, skipped',
        );
      }

      const org = await Org.findOne({ _id: orgId, isDeleted: false });

      if (!org) {
        throw new NotFoundError('Organisation not found');
      }

      // Update the onboarding status
      org.onBoardingStatus = status;
      await org.save();

      res.status(200).json({
        message: 'Onboarding status updated successfully',
        status: org.onBoardingStatus,
      });

      return;
    } catch (error) {
      next(error);
    }
  }
}
