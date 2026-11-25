import { getSlackUserLinkRepository } from '../../../libs/repositories/slack-user-link';
import {
  INBOX_ERASER,
  type InboxEraser,
} from '../../agent-harness/ports/inbox';
import {
  ENGINE_SIGNIN_ERASER,
  type EngineSignInEraser,
} from '../../agent-harness/ports/engine-signins';
import {
  PERSONAL_INSTANCE_ERASER,
  type PersonalInstanceEraser,
} from '../../agent-harness/ports/personal-instances';
import { Response, NextFunction } from 'express';
import {
  SCHEDULE_ERASER,
  ScheduleEraser,
} from '../../agent-harness/ports/schedules';
import bcrypt from 'bcryptjs';
import type { OrgQueryServiceEraser } from '../../agent-harness/ports/principal-erasure';
import { ORG_QUERY_SERVICE_ERASER } from '../../agent-harness/binders/tokens';
import { orgLifecycle } from '../../../libs/org-lifecycle';

import { getOrgRepository } from '../../../libs/repositories/org';
import { asAccount } from '../../../libs/context/orgContext';
import { createUnitOfWork } from '../../../libs/repositories/doc-store';
import { kickOutbox } from '../../../libs/services/outbox/outbox.dispatcher';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  detectRasterImageFormat,
  readImageUpload,
  sharp,
} from '../../../libs/utils/image.utils';
import { inject, injectable, optional } from 'inversify';
import {
  MODEL_ACCOUNT_ERASER,
  ModelAccountEraser,
} from '../../agent-harness/ports/model-accounts';
import {
  MEMORY_ERASER,
  MemoryEraser,
} from '../../agent-harness/ports/memory-store';
import {
  DATA_ERASER_EXTENSION,
  DataEraserExtension,
} from '../../agent-harness/ports/data-eraser-extension';
import { MailService } from '../services/mail.service';
import {
  PRINCIPAL_DELETION,
  type PrincipalDeletionService,
} from '../services/principal-deletion.service';
import { getBrandConfig } from '../../../libs/branding';
import { passwordValidator } from '../../auth/utils/passwordValidator';
import { SALT_ROUNDS } from '../../auth/controller/userAccount.controller';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
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
import { audit } from '../../../libs/services/audit';
import { withCause } from '../../../libs/errors/base.error';
import { hardenedProfile } from '../../../libs/utils/security-profile';
import { erasureOnDelete } from '../../agent-harness/config';

/**
 * How many pages of the query service's org erase one delete asks for. Each
 * page is idempotent, so a delete that runs out of pages leaves the org as it
 * is and the next delete continues where this one stopped.
 */
export const ORG_DELETE_QUERY_SERVICE_PAGES = 200;

/** The refusal of a delete whose typed name is not the org's. */
export const ORG_DELETE_NAME_MISMATCH = 'org_delete_name_mismatch';

/**
 * What an admin types to delete the org: its registered name, else its
 * display name, else its contact email (an individual account may have no
 * registered name). The UI asks for the same.
 */
export const orgConfirmationName = (org: {
  registeredName?: string | null;
  shortName?: string | null;
  contactEmail?: string | null;
}): string =>
  org.registeredName?.trim() ||
  org.shortName?.trim() ||
  org.contactEmail?.trim() ||
  '';

const ADDRESS_FIELDS = [
  'addressLine1',
  'city',
  'state',
  'country',
  'postCode',
] as const;

/** The onboarding status as an audit reason (snake_case). */
const ONBOARDING_REASONS: Readonly<Record<string, string>> = {
  configured: 'configured',
  notConfigured: 'not_configured',
  skipped: 'skipped',
};

@injectable()
export class OrgController {
  constructor(
    @inject('AppConfig') protected config: AppConfig,
    @inject('MailService') protected mailService: MailService,
    @inject('Logger') protected logger: Logger,
    @inject('EntitiesEventProducer')
    protected eventService: EntitiesEventProducer,
    @inject(MODEL_ACCOUNT_ERASER)
    @optional()
    protected modelAccounts: ModelAccountEraser | null = null,
    @inject(SCHEDULE_ERASER)
    @optional()
    protected schedules: ScheduleEraser | null = null,
    @inject(MEMORY_ERASER)
    @optional()
    protected memories: MemoryEraser | null = null,
    @inject(ENGINE_SIGNIN_ERASER)
    @optional()
    protected engineSignIns: EngineSignInEraser | null = null,
    @inject(PERSONAL_INSTANCE_ERASER)
    @optional()
    protected personalInstances: PersonalInstanceEraser | null = null,
    @inject(DATA_ERASER_EXTENSION)
    @optional()
    protected extension: DataEraserExtension | null = null,
    @inject(INBOX_ERASER)
    @optional()
    protected inbox: InboxEraser | null = null,
    @inject(ORG_QUERY_SERVICE_ERASER)
    @optional()
    protected queryService: OrgQueryServiceEraser | null = null,
    @inject(PRINCIPAL_DELETION)
    @optional()
    protected principalDeletion: Pick<
      PrincipalDeletionService,
      'requestOrg'
    > | null = null,
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
      const checkString =
        svgString.length > maxCheckLength
          ? svgString.substring(0, maxCheckLength)
          : svgString;

      // Use limited quantifiers [^>]{0,10000} to prevent ReDoS attacks
      // Check for dangerous patterns - throw error if found

      // Check for script tags (opening, closing, or incomplete)
      const scriptTagPattern = /<[\s]*script[^>]{0,10000}>/gi;
      const scriptClosingPattern = /<\/[\s]*script[^>]{0,10000}>/gi;
      if (
        scriptTagPattern.test(checkString) ||
        scriptClosingPattern.test(checkString)
      ) {
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
    const count = await getOrgRepository().count();

    res.status(200).json({ exists: count != 0 });
  }

  async createOrg(req: ContainerRequest, res: Response): Promise<void> {
    const container = req.container;
    if (!container) {
      throw new NotFoundError('Container not found');
    }

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

      const count = await getOrgRepository().count();
      if (count > 0) {
        throw new BadRequestError('There is already an organization');
      }
      const domain = this.getDomainFromEmail(contactEmail);
      if (!domain) {
        throw new BadRequestError(
          'Please specify a correct domain name. e.g. emailname@example.com',
        );
      }

      const hashedPassword = await bcrypt.hash(password, SALT_ROUNDS);

      // Either the organisation and the events that announce it both land, or
      // neither does (on a replica set). An organisation that exists without
      // those events is invisible to the permission graph. Without a replica
      // set the events are still recorded durably and delivered with retries;
      // only the atomicity is unavailable.
      const source = signupSource(
        req.body as { ref?: unknown; referrerDomain?: unknown },
      );
      const ids = getOrgRepository().allocateIds();
      // The unit runs as the new organisation and its first administrator: the ids are chosen first and the
      // context is bound from them (nothing here comes from the request's own identity).
      const { org, adminUserId } = await asAccount(
        ids.orgId,
        ids.adminUserId,
        'org-create',
        () =>
          createUnitOfWork(this.config.rsAvailable === 'true').run(
            async (tx) => {
              const created = await getOrgRepository().createWithAdmin(
                {
                  ids,
                  org: {
                    ...req.body,
                    domain,
                    onBoardingStatus: 'notConfigured', // Set default onboarding status when org is created
                    signupChannel: source.channel,
                    signupReferrerDomain: source.referrerDomain,
                  },
                  admin: { fullName: adminFullName, email: contactEmail },
                  passwordHash: hashedPassword,
                  ipAddress: req.ip,
                },
                tx,
              );
              const orgCreatedEvent: Event = {
                eventType: EventType.OrgCreatedEvent,
                timestamp: Date.now(),
                payload: {
                  orgId: created.org._id,
                  accountType: created.org.accountType,
                  registeredName: created.org.registeredName,
                } as OrgAddedEvent,
              };
              const adminUserEvent: Event = {
                eventType: EventType.NewUserEvent,
                timestamp: Date.now(),
                payload: {
                  orgId: created.org._id,
                  userId: created.adminUserId,
                  fullName: adminFullName,
                  email: created.adminEmail,
                  syncAction: 'none',
                } as UserAddedEvent,
              };
              await this.eventService.publishEvent(orgCreatedEvent, tx);
              await this.eventService.publishEvent(adminUserEvent, tx);
              return created;
            },
          ),
      );
      // The admin signs in and calls next; the poll interval would leave their graph user missing until then.
      kickOutbox();
      audit('org.create', {
        actor: adminUserId,
        org: org._id,
        outcome: 'success',
      });
      recordEvent(ORG_CREATED_EVENT, {
        orgId: org._id,
        userId: adminUserId,
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
            orgId: org._id,
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
      throw markClientSafe(
        withCause(
          new InternalServerError(
            serverFailureMessage('create the organisation'),
          ),
          error,
        ),
      );
    }
  }

  async getOrganizationById(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const orgId = req.user?.orgId;
    try {
      const org = await getOrgRepository().getLive(orgId as string);

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

      const org = await getOrgRepository().getLive(orgId as string);

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
      const updatedOrg = await getOrgRepository().update(
        orgId as string,
        updateData,
      );
      audit('org.update', { outcome: 'success' });

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
      const org = await getOrgRepository().getLive(orgId as string);

      if (!org) {
        throw new NotFoundError('Organisation not found');
      }

      // The server, not only the dialog, asks for the org's name, so a stray call cannot delete it. On the
      // compatibility profile (ADR-OPS-07) an API caller may leave it out, as before ORGDEL; one that sends it is held to it.
      const { confirmName } = (req.body ?? {}) as { confirmName?: unknown };
      const expected = orgConfirmationName(org);
      const confirmationRequired = hardenedProfile();
      if (
        (confirmName !== undefined || confirmationRequired) &&
        (typeof confirmName !== 'string' ||
          expected === '' ||
          confirmName.trim() !== expected)
      ) {
        throw new BadRequestError(
          "Type the organization's name exactly as shown to delete it.",
          { reasonCode: ORG_DELETE_NAME_MISMATCH },
        );
      }

      const hook = orgLifecycle().orgLifecycleHook;
      if (hook !== null) {
        res.status(200).json(
          await hook.deleteOrg({
            org,
            orgId: org._id,
            byUserId: String(req.user?.userId),
            config: {
              frontendUrl: this.config.frontendUrl,
              scopedJwtSecret: this.config.scopedJwtSecret,
            },
          }),
        );
        return;
      }

      // Erasure, beyond the soft delete a release without it did, runs where the harness is on for the org or the
      // install is hardened (ADR-OPS-07, ADR-OPS-08); otherwise the org's data stays as that release kept it.
      if (await erasureOnDelete(org._id)) {
        await this.eraseOrgData(org._id, String(req.user?.userId));
      }

      // Soft delete: set isDeleted to true
      const deleted = await getOrgRepository().softDelete(orgId as string);
      if (!deleted) {
        throw new NotFoundError('Organisation not found');
      }
      audit('org.delete', { outcome: 'success' });

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
        data: deleted,
      });
      return;
    } catch (error) {
      next(error);
    }
  }

  /** Before the org is marked deleted, so a failed erasure can be retried. */
  private async eraseOrgData(orgId: string, byUserId: string): Promise<void> {
    await this.modelAccounts?.eraseOrg(orgId);
    await this.schedules?.eraseOrg(orgId);
    await this.memories?.eraseOrg(orgId);
    // And what stores this build does not know of keep for it (`DataEraserExtension`).
    await this.extension?.eraseOrg(orgId);
    // And every person's own Claude sign-in (SIGNIN): records removed, live sandboxes signed out.
    await this.engineSignIns?.eraseOrg(orgId);
    // And every member's Inbox settings, run-notice claims and watch leases (G2).
    await this.inbox?.eraseOrg(orgId);
    // And every member's own toolset and MCP instances (main #4324).
    await this.personalInstances?.eraseOrg(orgId);
    // And what the query service holds for it: artifacts, chat attachments, skill candidates, agent versions.
    await this.eraseQueryServiceData(orgId);
    // And its Slack account links (ADR-SLACKID-01), which hold a person's email and Slack name.
    await getSlackUserLinkRepository().removeForOrg(orgId);
    // And the rest through the org's durable erasure job: its sessions (journals, payloads, workspaces), its harness
    // documents, its data keys and its audit chain, which no request could erase in one go.
    await this.principalDeletion?.requestOrg(orgId, byUserId);
  }

  /** Page by page until nothing is left; a page that fails, or too many pages, leave the org undeleted. */
  private async eraseQueryServiceData(orgId: string): Promise<void> {
    if (this.queryService === null) return;
    for (let page = 0; page < ORG_DELETE_QUERY_SERVICE_PAGES; page += 1) {
      if (!(await this.queryService.eraseOrg(orgId)).remaining) return;
    }
    throw new ServiceUnavailableError(
      "The organization's data is still being erased. Delete it again to continue.",
    );
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
      await getOrgRepository().setLogo(orgId as string, base64Logo, mimeType);
      audit('org.logo.update', { outcome: 'success', reason: 'set' });

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

      const orgLogo = await getOrgRepository().getLogo(orgId as string);

      if (!orgLogo || !orgLogo.logo) {
        res.status(204).end();
        return;
      }

      const logoBuffer = Buffer.from(orgLogo.logo, 'base64');
      if (orgLogo.mimeType) {
        res.setHeader('Content-Type', orgLogo.mimeType);
      }
      // An SVG logo opened as a page must not run script on this origin.
      res.setHeader('X-Content-Type-Options', 'nosniff');
      res.setHeader(
        'Content-Security-Policy',
        "default-src 'none'; style-src 'unsafe-inline'; sandbox",
      );

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
      const orgLogo = await getOrgRepository().removeLogo(orgId as string);
      if (!orgLogo) {
        throw new NotFoundError('Organisation logo not found');
      }
      audit('org.logo.update', { outcome: 'success', reason: 'removed' });

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

      const org = await getOrgRepository().getOnboardingStatus(orgId as string);

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

      const org = await getOrgRepository().setOnboardingStatus(
        orgId as string,
        status,
      );

      if (!org) {
        throw new NotFoundError('Organisation not found');
      }
      audit('org.onboarding.update', {
        outcome: 'success',
        reason: ONBOARDING_REASONS[status as string],
      });

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
