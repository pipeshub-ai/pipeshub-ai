import axios, { AxiosError } from 'axios';
import { inject, injectable } from 'inversify';
import {
  BadRequestError,
  InternalServerError,
} from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
import { Logger } from '../../../libs/services/logger.service';
@injectable()
export class ConfigService {
  constructor(
    @inject('AppConfig') private appConfig: AppConfig,
    @inject('Logger') private logger: Logger,
  ) {}
  async updateConfig(scopedToken: string) {
    try {
      let config = {
        method: 'post',
        url: `${this.appConfig.iamBackend}/api/v1/users/updateAppConfig`,
        headers: {
          Authorization: `Bearer ${scopedToken}`,
          'Content-Type': 'application/json',
        },
      };

      let response = await axios(config);

      if (response.status != 200) {
        throw new BadRequestError('Error setting user config');
      }
      this.logger.debug('user container config updated');

      config = {
        method: 'post' as const,
        url: `${this.appConfig.communicationBackend}/api/v1/mail/updateSmtpConfig`,
        headers: {
          Authorization: `Bearer ${scopedToken}`,
          'Content-Type': 'application/json',
        },
      };

      response = await axios(config);

      if (response.status != 200) {
        throw new BadRequestError('Error setting smtp config');
      }
      this.logger.debug('smtp container config updated');

      config = {
        method: 'post' as const,
        url: `${this.appConfig.authBackend}/api/v1/saml/updateAppConfig`,
        headers: {
          Authorization: `Bearer ${scopedToken}`,
          'Content-Type': 'application/json',
        },
      };

      response = await axios(config);

      if (response.status != 200) {
        throw new BadRequestError('Error setting auth config');
      }
      this.logger.debug('auth container config updated');

      config = {
        method: 'post' as const,
        url: `${this.appConfig.storageBackend}/api/v1/document/updateAppConfig`,
        headers: {
          Authorization: `Bearer ${scopedToken}`,
          'Content-Type': 'application/json',
        },
      };

      response = await axios(config);

      if (response.status != 200) {
        throw new BadRequestError('Error setting storage config');
      }
      this.logger.debug('storage container config updated');

      config = {
        method: 'post' as const,
        url: `${this.appConfig.tokenBackend}/api/v1/connectors/updateAppConfig`,
        headers: {
          Authorization: `Bearer ${scopedToken}`,
          'Content-Type': 'application/json',
        },
      };

      response = await axios(config);

      if (response.status != 200) {
        throw new BadRequestError('Error setting token config');
      }
      this.logger.debug('token container config updated');

      config = {
        method: 'post' as const,
        url: `${this.appConfig.esBackend}/api/v1/search/updateAppConfig`,
        headers: {
          Authorization: `Bearer ${scopedToken}`,
          'Content-Type': 'application/json',
        },
      };

      response = await axios(config);

      if (response.status != 200) {
        throw new BadRequestError('Error setting es config');
      }
      this.logger.debug('es container config updated');

      return { statusCode: response.status, data: response.data };
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new AxiosError(
          error.response?.data?.message || 'Failed to update App Config',
          error.code,
          error.config,
          error.request,
          error.response,
        );
      }
      throw new InternalServerError(
        error instanceof Error ? error.message : 'Unexpected error occurred',
      );
    }
  }
}
