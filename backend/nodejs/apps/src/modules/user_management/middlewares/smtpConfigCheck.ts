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
import { mailConfigUrl } from '../constants/constants';

export const smtpConfigCheck =
  (cmBackend: string) =>
  async (
    req: AuthenticatedUserRequest,
    _res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      let configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
        {
          uri: `${cmBackend}/${mailConfigUrl}`,
          method: HttpMethod.GET,
          headers: req.headers as Record<string, string>,
        };
      const getCredentialsCommand = new ConfigurationManagerServiceCommand(
        configurationManagerCommandOptions,
      );
      let response = await getCredentialsCommand.execute();
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
        throw new NotFoundError('Smtp Host is missing');
      }
      if (!credentialsData.port) {
        throw new NotFoundError('Smtp Port is missing');
      }
      if (!credentialsData.fromEmail) {
        throw new NotFoundError('Smtp From Email is missing');
      }
      next();
    } catch (error) {
      next(error);
    }
  };
