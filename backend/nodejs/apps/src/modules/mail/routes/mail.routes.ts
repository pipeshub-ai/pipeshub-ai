import { Router, Response, NextFunction } from 'express';

import { attachContainerMiddleware } from '../../auth/middlewares/attachContainer.middleware';
import { Container } from 'inversify';
import { MailController } from '../controller/mail.controller';
import { z } from 'zod';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AuthenticatedServiceRequest } from '../../../libs/middlewares/types';
import { smtpConfigChecker } from '../middlewares/checkSmtpConfig';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { AppConfig, loadAppConfig } from '../../tokens_manager/config/config';
export const smtpConfigSchema = z.object({
  body: z.object({
    host: z.string().min(1, { message: 'SMTP host is required' }),
    port: z.number().min(1, { message: 'SMTP port is required' }),
    username: z.string().optional(),
    password: z.string().optional(),
    fromEmail: z.string().min(1, { message: 'SMTP from is required' }),
  }),
});
export function createMailServiceRouter(container: Container) {
  const router = Router();
  router.use(attachContainerMiddleware(container));
  let mailController = container.get<MailController>('MailController');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  router.post(
    '/emails/sendEmail',
    smtpConfigChecker,
    authMiddleware.scopedTokenValidator(TokenScopes.SEND_MAIL),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        await mailController.sendMail(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/updateSmtpConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    async (
      _req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const updatedConfig: AppConfig = await loadAppConfig();

        container
          .rebind<AppConfig>('AppConfig')
          .toDynamicValue(() => updatedConfig);

        container
          .rebind<MailController>('MailController')
          .toDynamicValue(() => {
            return new MailController(updatedConfig, container.get('Logger'));
          });
        mailController = container.get<MailController>('MailController');

        res.status(200).json({
          message: 'SMTP configuration updated successfully',
          smtp: updatedConfig.smtp,
        });
        return;
      } catch (error) {
        next(error);
      }
    },
  );
  return router;
}
