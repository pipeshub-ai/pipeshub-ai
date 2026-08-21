import { Response, NextFunction } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import {
  ConfigurationManagerCommandOptions,
  ConfigurationManagerServiceCommand,
} from '../../../libs/commands/configuration_manager/cm.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { fetchConfigJwtGenerator } from '../../../libs/utils/createJwt';
import { mailConfigInternalUrl } from '../constants/constants';

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
        throw new InternalServerError('Error getting smtp config');
      }
      if (response.statusCode !== 200) {
        throw new InternalServerError(
          'Error getting smtp config',
          response?.data?.error?.message,
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
