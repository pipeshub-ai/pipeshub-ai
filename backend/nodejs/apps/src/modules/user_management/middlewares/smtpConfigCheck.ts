import { Response, NextFunction } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';
import {
  markClientSafe,
  serverFailureMessage,
} from '../../../libs/errors/reader-friendly';
import {
  ConfigurationManagerCommandOptions,
  ConfigurationManagerServiceCommand,
} from '../../../libs/commands/configuration_manager/cm.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { fetchConfigJwtGenerator } from '../../../libs/utils/createJwt';
import { mailConfigInternalUrl } from '../constants/constants';

const logger = Logger.getInstance({
  service: 'SMTP Config Check',
});

export const smtpConfigCheck =
  (cmBackend: string, scopedJwtSecret: string) =>
  async (
    req: AuthenticatedUserRequest,
    _res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      if (!userId || !orgId) {
        throw new NotFoundError('Account not found');
      }

      // FETCH_CONFIG scoped token — do not forward the caller's user JWT.
      // GET /smtpConfig is admin-only; members inviting others would fail that path.
      const authToken = fetchConfigJwtGenerator(
        userId,
        orgId,
        scopedJwtSecret,
      );

      const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
        {
          uri: `${cmBackend}/${mailConfigInternalUrl}`,
          method: HttpMethod.GET,
          headers: {
            Authorization: `Bearer ${authToken}`,
          },
        };
      const getCredentialsCommand = new ConfigurationManagerServiceCommand(
        configurationManagerCommandOptions,
      );
      const response = await getCredentialsCommand.execute();
      if (
        !response ||
        typeof response !== 'object' ||
        typeof response.statusCode !== 'number'
      ) {
        logger.error('The configuration service gave an unreadable answer');
        throw markClientSafe(
          new InternalServerError(serverFailureMessage('check the email settings')),
        );
      }
      if (response.statusCode !== 200) {
        logger.error('Reading the email settings failed', {
          statusCode: response.statusCode,
          upstream: response?.data?.error?.message,
        });
        throw markClientSafe(
          new InternalServerError(serverFailureMessage('check the email settings')),
        );
      }
      const credentialsData = response.data;
      if (!credentialsData) {
        throw new NotFoundError('Smtp Configuration not found');
      }
      if (!credentialsData.host) {
        throw new NotFoundError('Smtp not configured: Host is missing');
      }
      if (!credentialsData.port) {
        throw new NotFoundError('Smtp not configured: Port is missing');
      }
      if (!credentialsData.fromEmail) {
        throw new NotFoundError('Smtp not configured: From Email is missing');
      }
      next();
    } catch (error) {
      next(error);
    }
  };
