import axios, { AxiosError } from 'axios';
import { inject, injectable } from 'inversify';
import { InternalServerError } from '../../../libs/errors/http.errors';
import { HttpError } from '../../../libs/errors/http.errors';
import {
  keepDeliberateWording,
  markClientSafe,
  serverFailureMessage,
} from '../../../libs/errors/reader-friendly';
import { AppConfig } from '../../tokens_manager/config/config';
import { withCause } from '../../../libs/errors/base.error';
@injectable()
export class AuthService {
  constructor(@inject('AppConfig') private authConfig: AppConfig) {}
  async passwordMethodEnabled(authServiceToken: string) {
    try {
      const config = {
        method: 'get',
        url: `${this.authConfig.authBackend}/api/v1/userAccount/internal/password/check`,
        headers: {
          Authorization: `Bearer ${authServiceToken}`,
          'Content-Type': 'application/json',
        },
      };

      const response = await axios(config);
      return { statusCode: response.status, data: response.data };
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new AxiosError(
          error.response?.data?.message ||
            'Failed to get response to check password method is enabled or not',
          error.code,
          error.config,
          error.request,
          error.response,
        );
      }
      if (error instanceof HttpError) throw keepDeliberateWording(error);
      throw markClientSafe(
        withCause(
          new InternalServerError(
            serverFailureMessage('check how you can sign in'),
          ),
          error,
        ),
      );
    }
  }
}
