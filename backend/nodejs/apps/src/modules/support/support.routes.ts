import { RequestHandler, Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../libs/middlewares/auth.middleware';
import { FileProcessingType } from '../../libs/middlewares/file_processor/fp.constant';
import { FileProcessorFactory } from '../../libs/middlewares/file_processor/fp.factory';
import { createKeyedRateLimiter } from '../../libs/middlewares/rate-limit.middleware';
import { ValidationMiddleware } from '../../libs/middlewares/validation.middleware';
import { Logger } from '../../libs/services/logger.service';
import { MailSenderService } from '../mail/services/mail.sender.service';
import {
  SUPPORT_ATTACHMENT_EXTENSIONS,
  SUPPORT_ATTACHMENT_MIME_BY_EXTENSION,
  SUPPORT_LIMITS,
} from './support.config';
import {
  createFeedback,
  createSupportRequest,
  getFeedbackSmtpStatus,
  getSupportContext,
} from './support.controller';
import { SupportService } from './support.service';
import {
  createFeedbackSchema,
  createSupportRequestSchema,
} from './support.validators';

const HOUR_MS = 60 * 60 * 1000;

function attachmentUpload(): RequestHandler[] {
  return FileProcessorFactory.createBufferUploadProcessor({
    fieldName: 'attachments',
    allowedMimeTypes: [
      ...new Set(Object.values(SUPPORT_ATTACHMENT_MIME_BY_EXTENSION)),
    ],
    allowedExtensions: [...SUPPORT_ATTACHMENT_EXTENSIONS],
    resolveMimeType: (extension: string) =>
      SUPPORT_ATTACHMENT_MIME_BY_EXTENSION[extension] ?? null,
    maxFilesAllowed: SUPPORT_LIMITS.maxAttachments,
    isMultipleFilesAllowed: true,
    processingType: FileProcessingType.BUFFER,
    maxFileSize: SUPPORT_LIMITS.maxAttachmentBytes,
    strictFileUpload: false,
  }).getMiddleware;
}

/** Each send emails the support address from this workspace's SMTP, so each person gets a few an hour. */
function hourlyLimit(logger: Logger, prefix: string): RequestHandler {
  return createKeyedRateLimiter(logger, {
    prefix,
    maxRequestsPerMinute: SUPPORT_LIMITS.requestsPerHour,
    windowMs: HOUR_MS,
    message: 'Too many requests in the last hour. Try again later.',
  });
}

function setUp(container: Container): {
  auth: RequestHandler;
  logger: Logger;
  service: SupportService;
} {
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const logger = container.get<Logger>('Logger');
  return {
    auth: authMiddleware.authenticate.bind(authMiddleware),
    logger,
    service: new SupportService(
      container.get<MailSenderService>(MailSenderService),
      logger,
    ),
  };
}

/** Contact support: `container` is the mail service container. */
export function createSupportRouter(container: Container): Router {
  const router = Router();
  const { auth, logger, service } = setUp(container);

  router.get('/context', auth, (req, res, next) =>
    getSupportContext(req, res, next, service),
  );

  router.post(
    '/',
    auth,
    ...attachmentUpload(),
    ValidationMiddleware.validate(createSupportRequestSchema),
    hourlyLimit(logger, 'support'),
    (req, res, next) => createSupportRequest(req, res, next, service),
  );

  return router;
}

/** Send feedback: `container` is the mail service container. */
export function createFeedbackRouter(container: Container): Router {
  const router = Router();
  const { auth, logger, service } = setUp(container);

  router.get('/smtp-status', auth, (req, res, next) =>
    getFeedbackSmtpStatus(req, res, next, service),
  );

  router.post(
    '/',
    auth,
    ...attachmentUpload(),
    ValidationMiddleware.validate(createFeedbackSchema),
    hourlyLimit(logger, 'feedback'),
    (req, res, next) => createFeedback(req, res, next, service),
  );

  return router;
}
