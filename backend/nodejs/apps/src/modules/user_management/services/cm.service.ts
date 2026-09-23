import axios, { AxiosError } from 'axios';
import { injectable } from 'inversify';
import { InternalServerError } from '../../../libs/errors/http.errors';
import { HttpError } from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';
import {
  keepDeliberateWording,
  markClientSafe,
  serverFailureMessage,
} from '../../../libs/errors/reader-friendly';

interface ConfigManagerResponse {
  statusCode: number;
  data: any;
}
const logger = Logger.getInstance({
  service: 'User Management Config Service',
});

@injectable()
export class ConfigurationManagerService {
  constructor() {}

  async setConfig(
    cmBackendUrl: string,
    configUrlPath: string,
    scopedToken: string,
    body: Record<string, any>,
  ): Promise<ConfigManagerResponse> {
    try {
      const config = {
        method: 'post' as const,
        url: `${cmBackendUrl}/${configUrlPath}`,
        headers: {
          Authorization: `Bearer ${scopedToken}`,
          'Content-Type': 'application/json',
        },
        data: body,
      };

      const response = await axios(config);
      return { statusCode: 200, data: response.data };
    } catch (error) {
      if (axios.isAxiosError(error)) {
        let errorMessage = 'Error setting config';
        if (error.code === 'ECONNABORTED') {
          errorMessage = 'Request timed out';
        } else if (error.response) {
          errorMessage = error.response?.data?.message || errorMessage;
        }
        throw new AxiosError(
          errorMessage,
          error.code,
          error.config,
          error.request,
          error.response,
        );
      }
      if (error instanceof HttpError) throw keepDeliberateWording(error);
      logger.error('Writing the configuration failed', { error });
      throw markClientSafe(
        new InternalServerError(serverFailureMessage('save that setting')),
      );
    }
  }
}
