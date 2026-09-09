import { Router } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { FileProcessorFactory } from '../../../libs/middlewares/file_processor/fp.factory';
import { FileProcessingType } from '../../../libs/middlewares/file_processor/fp.constant';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  MAX_FEEDBACK_ATTACHMENTS,
  MAX_FEEDBACK_ATTACHMENT_BYTES,
} from '../schema/feedback.schema';
import { createFeedbackSchema } from '../validators/feedback.validators';
import { FeedbackService } from '../service/feedback.service';
import { createFeedback } from '../controllers/feedback.controller';

export function createFeedbackRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const logger = container.get<Logger>('Logger');
  const auth = authMiddleware.authenticate.bind(authMiddleware);

  router.post(
    '/',
    auth,
    ...FileProcessorFactory.createBufferUploadProcessor({
      fieldName: 'attachments',
      allowedMimeTypes: [
        'image/jpeg',
        'image/jpg',
        'image/png',
        'image/webp',
        'image/gif',
        'application/pdf',
        'text/plain',
        'text/csv',
      ],
      allowedExtensions: ['jpg', 'jpeg', 'png', 'webp', 'gif', 'pdf', 'txt', 'log', 'csv'],
      resolveMimeType: (extension: string) => {
        const byExtension: Record<string, string> = {
          jpg: 'image/jpeg',
          jpeg: 'image/jpeg',
          png: 'image/png',
          webp: 'image/webp',
          gif: 'image/gif',
          pdf: 'application/pdf',
          txt: 'text/plain',
          log: 'text/plain',
          csv: 'text/csv',
        };
        return byExtension[extension] ?? null;
      },
      maxFilesAllowed: MAX_FEEDBACK_ATTACHMENTS,
      isMultipleFilesAllowed: true,
      processingType: FileProcessingType.BUFFER,
      maxFileSize: MAX_FEEDBACK_ATTACHMENT_BYTES,
      strictFileUpload: false,
    }).getMiddleware,
    ValidationMiddleware.validate(createFeedbackSchema),
    (req, res, next) => {
      const service = new FeedbackService(
        container.get<AppConfig>('AppConfig'),
        logger,
      );
      return createFeedback(req, res, next, service);
    },
  );

  return router;
}
