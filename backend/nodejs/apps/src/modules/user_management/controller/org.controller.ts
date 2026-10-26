import { Response, NextFunction } from 'express';
import mongoose from 'mongoose';
import bcrypt from 'bcryptjs';

import { Org } from '../schema/org.schema';
import { Users } from '../schema/users.schema';
import { UserGroups } from '../schema/userGroup.schema';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { OrgLogos } from '../schema/orgLogo.schema';
import {
  detectRasterImageFormat,
  readImageUpload,
  sharp,
} from '../../../libs/utils/image.utils';
import { inject, injectable } from 'inversify';
import { MailService } from '../services/mail.service';
import { getBrandConfig } from '../../../libs/branding';
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
import {
  markClientSafe,
  serverFailureMessage,
} from '../../../libs/errors/reader-friendly';
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
import { recordEvent } from '../../../libs/services/telemetry/event-buffer';
import {
  domainFromEmail,
  signupSource,
} from '../../../libs/services/telemetry/identity';
import { ORG_CREATED_EVENT } from '../constants/constants';

const ADDRESS_FIELDS = [
  'addressLine1',
  'city',
  'state',
  'country',
  'postCode',
] as const;

@injectable()
export class OrgController {
  constructor(
    @inject('AppConfig') protected config: AppConfig,
    @inject('MailService') protected mailService: MailService,
    @inject('Logger') protected logger: Logger,
    @inject('EntitiesEventProducer')
    protected eventService: EntitiesEventProducer,
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
  protected validateSVG(svgBuffer: Buffer): void {
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

    let session: mongoose.ClientSession | null = null;
    try {
      const { contactEmail, adminFullName, password, sendEmail } = req.body as {
        contactEmail: string;
        adminFullName: string;
        password: string;
        sendEmail: boolean;
      };

      if (!passwordValidator(password)) {
        throw new BadRequestError(
          'Password should have minimum 8 characters with at least one uppercase, one lowercase, one number and one special character, and be no longer than 72 bytes',
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

      const source = signupSource(
        req.body as { ref?: unknown; referrerDomain?: unknown },
      );
      const org = new Org({
        ...req.body,
        domain,
        onBoardingStatus: 'notConfigured', // Set default onboarding status when org is created
        signupChannel: source.channel,
        signupReferrerDomain: source.referrerDomain,
      });

      const adminUser = new Users({
        fullName: adminFullName,
        email: contactEmail,
        orgId: org._id,
        role: 'admin',
      });

      const hashedPassword = await bcrypt.hash(password, SALT_ROUNDS);

      const adminUserCredentials = new UserCredentials({
        userId: adminUser._id,
        orgId: org._id,
        isDeleted: false,
        hashedPassword,
        ipAddress: req.ip,
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

      // Built before the save so the transactional branch can store them with
      // the same commit. An organisation that exists without the events that
      // announce it is invisible to the permission graph, which is the pair
      // this transaction is here to keep together.
      const orgCreatedEvent: Event = {
        eventType: EventType.OrgCreatedEvent,
        timestamp: Date.now(),
        payload: {
          orgId: org._id.toString(),
          accountType: org.accountType,
          registeredName: org.registeredName,
        } as OrgAddedEvent,
      };
      const adminUserEvent: Event = {
        eventType: EventType.NewUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: adminUser.orgId.toString(),
          userId: adminUser._id.toString(),
          fullName: adminUser.fullName,
          email: adminUser.email,
          syncAction: 'none',
        } as UserAddedEvent,
      };

      const rsAvailable = this.config.rsAvailable === 'true';
      if (rsAvailable) {
        session = await mongoose.startSession();

        session.startTransaction();
        await orgAuthConfig.save({ session });
        await allUsersGroup.save({ session });
        await standardUsersGroup.save({ session });
        await adminUser.save({ session });
        await adminUserCredentials.save({ session });
        await org.save({ session });
        // Inside the transaction: either the organisation and the events that
        // describe it both land, or neither does.
        await this.eventService.publishEvent(orgCreatedEvent, session);
        await this.eventService.publishEvent(adminUserEvent, session);
        await session.commitTransaction();
      } else {
        await orgAuthConfig.save();
        await allUsersGroup.save();
        await standardUsersGroup.save();
        await adminUser.save();
        await adminUserCredentials.save();
        await org.save();
        // No replica set, so no transaction to join. The events are still
        // recorded durably and delivered with retries; only the atomicity is
        // unavailable here.
        await this.eventService.publishEvent(orgCreatedEvent);
        await this.eventService.publishEvent(adminUserEvent);
      }

      recordEvent(ORG_CREATED_EVENT, {
        orgId: (org._id as mongoose.Types.ObjectId).toString(),
        userId: (adminUser._id as mongoose.Types.ObjectId).toString(),
        email: contactEmail,
        domain: domainFromEmail(contactEmail),
        fullName: adminFullName,
        channel: source.channel,
        ...(source.referrerDomain !== undefined
          ? { referrer_domain: source.referrerDomain }
          : {}),
      });

      if (sendEmail) {
        await this.mailService.sendMail({
          emailTemplateType: 'accountCreation',
          initiator: {
            jwtAuthToken: mailJwtGenerator(
              contactEmail,
              this.config.scopedJwtSecret,
            ),
            orgId: org._id.toString(),
          },
          usersMails: [contactEmail],
          subject: 'New Org Account Creation',
          templateData: {
            invitee: getBrandConfig().productName,
            name: 'User',
            orgName: org.registeredName,
            link: `${this.config.frontendUrl}`,
          },
        });
      }

      res.status(200).json(org);
    } catch (error) {
      if (error instanceof BadRequestError || error instanceof NotFoundError) {
        throw error;
      }
      this.logger.error('Creating the organisation failed', { error });
      throw markClientSafe(
        new InternalServerError(serverFailureMessage('create the organisation')),
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
    this.logger.debug(orgId);
    try {
      const org = await Org.findOne({ _id: new mongoose.Types.ObjectId(orgId), isDeleted: false });

      if (!org) {
        throw new NotFoundError('Organisation not found');
      }
      res.status(200).json(org);
      return;
    } catch (error) {
      next(error);
    }
  }

  /** A request-body field that, when present, must be a string. */
  private optionalString(field: string, value: unknown): string | undefined {
    if (value === undefined || value === null) return undefined;
    if (typeof value !== 'string') {
      throw new BadRequestError(`${field} must be a string`);
    }
    return value;
  }

  /**
   * The address fields of a request body, each a string. Anything else (an
   * operator object, an array, a number) is refused rather than stored.
   */
  private pickAddress(value: unknown): Record<string, string> | undefined {
    if (value === undefined || value === null) return undefined;
    if (typeof value !== 'object' || Array.isArray(value)) {
      throw new BadRequestError('permanentAddress must be an object');
    }
    const address: Record<string, string> = {};
    for (const field of ADDRESS_FIELDS) {
      const part = (value as Record<string, unknown>)[field];
      if (part === undefined) continue;
      if (typeof part !== 'string') {
        throw new BadRequestError(`permanentAddress.${field} must be a string`);
      }
      address[field] = part;
    }
    return address;
  }

  private savedAddress(org: unknown): Record<string, string> {
    const doc = org as { toObject?: () => Record<string, unknown> };
    const plain = doc.toObject?.() ?? (org as Record<string, unknown>);
    const stored = plain.permanentAddress;
    const saved: Record<string, string> = {};
    if (stored === null || typeof stored !== 'object') return saved;
    for (const field of ADDRESS_FIELDS) {
      const part = (stored as Record<string, unknown>)[field];
      if (typeof part === 'string') saved[field] = part;
    }
    return saved;
  }

  async updateOrganizationDetails(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const { contactEmail, registeredName, shortName, permanentAddress } =
      req.body as Record<string, unknown>;

    try {
      // The route schema already forces these shapes. Checked again here so a
      // query operator ({ "$ne": null }, { "$where": ... }) can never be spread
      // into the update, whatever route reaches this method.
      const email = this.optionalString('contactEmail', contactEmail);
      const regName = this.optionalString('registeredName', registeredName);
      const short = this.optionalString('shortName', shortName);
      const address = this.pickAddress(permanentAddress);

      const orgId = req.user?.orgId;

      const org = await Org.findOne({ _id: new mongoose.Types.ObjectId(orgId), isDeleted: false });

      if (!org) {
        throw new NotFoundError('Organisation not found');
      }

      // Update only the fields that are provided in the request body
      const updateData: Record<string, string | Record<string, string>> = {};

      if (email) updateData.contactEmail = email;
      if (regName) updateData.registeredName = regName;
      if (short) updateData.shortName = short;
      // Merge the supplied fields into the saved address so a partial address
      // keeps the fields it omits. A whole-object $set also works when the
      // stored value is null, where a dotted path would fail.
      if (address && Object.keys(address).length > 0) {
        const saved = this.savedAddress(org);
        updateData.permanentAddress = { ...saved, ...address };
      }

      // Perform the update
      const updatedOrg = await Org.findByIdAndUpdate(orgId, updateData, {
        returnDocument: 'after',
      });

      await this.eventService.start();
      let event: Event = {
        eventType: EventType.OrgUpdatedEvent,
        timestamp: Date.now(),
        payload: {
          orgId: org._id.toString(),
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
      const org = await Org.findOne({ _id: new mongoose.Types.ObjectId(orgId), isDeleted: false });

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
          orgId: org._id.toString(),
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
      const upload = readImageUpload(req.body as unknown);
      const orgId = req.user?.orgId;
      if (upload === undefined) {
        throw new BadRequestError('Organisation logo file is required');
      }

      const logoBuffer = upload.buffer;
      // The declared MIME type is client-controlled. Raster vs SVG is decided
      // from the bytes, so an SVG labelled image/png is never decoded.
      const rasterFormat = detectRasterImageFormat(logoBuffer);
      let processedBuffer: Buffer;
      let mimeType: string;

      if (rasterFormat !== null) {
        const MAX_LOGO_SIZE_BYTES = 2 * 1024 * 1024;
        const INITIAL_JPEG_QUALITY = 100;
        const MIN_JPEG_QUALITY = 10;
        const JPEG_QUALITY_STEP = 10;

        let quality = INITIAL_JPEG_QUALITY + JPEG_QUALITY_STEP;

        do {
          quality -= JPEG_QUALITY_STEP;
          processedBuffer = await sharp(logoBuffer)
            .jpeg({ quality })
            .toBuffer();
        } while (
          processedBuffer.length > MAX_LOGO_SIZE_BYTES &&
          quality > MIN_JPEG_QUALITY
        );

        mimeType = 'image/jpeg';
      } else if (upload.mimetype === 'image/svg+xml') {
        // SVG is stored as text after a string check. It is not decoded.
        this.validateSVG(logoBuffer);
        processedBuffer = logoBuffer;
        mimeType = 'image/svg+xml';
      } else {
        throw new BadRequestError(
          'Organisation logo must be a PNG, JPEG, WebP, GIF or SVG image',
        );
      }

      const base64Logo = processedBuffer.toString('base64');
      await OrgLogos.findOneAndUpdate(
        {
          orgId,
        },
        { orgId, logo: base64Logo, mimeType },
        { returnDocument: 'after', upsert: true },
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
