import { Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { FileProcessorFactory } from '../../../libs/middlewares/file_processor/fp.factory';
import { FileProcessingType } from '../../../libs/middlewares/file_processor/fp.constant';
import { Logger } from '../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  MAX_FEEDBACK_ATTACHMENTS,
  MAX_FEEDBACK_ATTACHMENT_BYTES,
  feedbackAllowedExtensions,
  feedbackMimeTypeByExtension,
  feedbackUploadMimeTypes,
} from '../schema/feedback.schema';
import { createFeedbackSchema } from '../validators/feedback.validators';
import { FeedbackService } from '../service/feedback.service';
import { createFeedback, getSmtpStatus } from '../controllers/feedback.controller';

export function createFeedbackRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const logger = container.get<Logger>('Logger');
  const auth = authMiddleware.authenticate.bind(authMiddleware);

  const feedbackService = new FeedbackService(
    container.get<AppConfig>('AppConfig'),
    logger,
    container.get<KeyValueStoreService>('KeyValueStoreService'),
  );

  router.get('/smtp-status', auth, (req, res, next) => {
    return getSmtpStatus(req, res, next, feedbackService);
  });

  router.post(
    '/',
    auth,
    ...FileProcessorFactory.createBufferUploadProcessor({
      fieldName: 'attachments',
      allowedMimeTypes: feedbackUploadMimeTypes,
      allowedExtensions: [...feedbackAllowedExtensions],
      resolveMimeType: (extension: string) =>
        feedbackMimeTypeByExtension[extension] ?? null,
      maxFilesAllowed: MAX_FEEDBACK_ATTACHMENTS,
      isMultipleFilesAllowed: true,
      processingType: FileProcessingType.BUFFER,
      maxFileSize: MAX_FEEDBACK_ATTACHMENT_BYTES,
      strictFileUpload: false,
    }).getMiddleware,
    ValidationMiddleware.validate(createFeedbackSchema),
    (req, res, next) => {
      return createFeedback(req, res, next, feedbackService);
    },
  );

  return router;
}
