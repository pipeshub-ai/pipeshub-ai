import axios, { AxiosError } from 'axios';
import { inject, injectable } from 'inversify';
import {
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { HttpError } from '../../../libs/errors/http.errors';
import {
  keepDeliberateWording,
  markClientSafe,
  serverFailureMessage,
} from '../../../libs/errors/reader-friendly';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';

@injectable()
export class IamService {
  constructor(
    @inject('AppConfig') private authConfig: AppConfig,
    @inject('Logger') private logger: Logger,
  ) {}
  async createOrg(orgData: any, authServiceToken: string) {
    try {
      const config = {
        method: 'post',
        url: `${this.authConfig.iamBackend}/api/v1/orgs/`,
        headers: {
          Authorization: `Bearer ${authServiceToken}`,
          'Content-Type': 'application/json',
        },
        data: {
          contactEmail: orgData.contactEmail,
          registeredName: orgData.registeredName,
          adminFullName: orgData.adminFullName,
          sendEmail: orgData.sendEmail,
        },
      };

      const response = await axios(config);
      return { statusCode: response.status, data: response.data };
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new AxiosError(
          error.response?.data?.message || 'Failed to create organization',
          error.code,
          error.config,
          error.request,
          error.response,
        );
      }
      if (error instanceof HttpError) throw keepDeliberateWording(error);
      this.logger.error('Creating the organisation failed', { error });
      throw markClientSafe(
        new InternalServerError(serverFailureMessage('create the organisation')),
      );
    }
  }

  //Create a new user via IAM backend
  //Note: Currently not used for SAML JIT provisioning as it requires admin authentication and doesn't handle group assignment.
  //SAML JIT uses direct DB access via userController.provisionSamlUser()
  async createUser(userData: any, authServiceToken: string) {
    try {
      const config = {
        method: 'post',
        url: `${this.authConfig.iamBackend}/api/v1/users/`,
        headers: {
          Authorization: `Bearer ${authServiceToken}`,
          'Content-Type': 'application/json',
        },
        data: { ...userData },
      };

      const response = await axios(config);
      return { statusCode: response.status, data: response.data };
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new AxiosError(
          error.response?.data?.message || 'Failed to create user',
          error.code,
          error.config,
          error.request,
          error.response,
        );
      }
      if (error instanceof HttpError) throw keepDeliberateWording(error);
      this.logger.error('Creating the user failed', { error });
      throw markClientSafe(
        new InternalServerError(serverFailureMessage('create the account')),
      );
    }
  }

  async getUserByEmail(email: string, authServiceToken: string) {
    try {
      const config = {
        method: 'get',
        url: `${this.authConfig.iamBackend}/api/v1/users/email/exists`,
        headers: {
          Authorization: `Bearer ${authServiceToken}`,
          'Content-Type': 'application/json',
        },
        data: {
          email: email,
        },
      };

      const response = await axios(config);
      const users = response.data;

      if (!users || users.length === 0) {
        // throw new NotFoundError('Account not found');
        return { statusCode: 404, data: { message: 'Account not found' } };
      }
      this.logger.debug(users);
      return { statusCode: 200, data: users[0] };
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new AxiosError(
          error.response?.data?.message || 'Error getting user',
          error.code,
          error.config,
          error.request,
          error.response,
        );
      }
      if (error instanceof HttpError) throw keepDeliberateWording(error);
      this.logger.error('Looking the user up by email failed', { error });
      throw markClientSafe(
        new InternalServerError(serverFailureMessage('look up the account')),
      );
    }
  }

  async getUserById(userId: string, authServiceToken: string) {
    try {
      const config = {
        method: 'get',
        url: `${this.authConfig.iamBackend}/api/v1/users/internal/${userId}`,
        headers: {
          Authorization: `Bearer ${authServiceToken}`,
          'Content-Type': 'application/json',
        },
      };

      const response = await axios(config);

      const user = response.data;
      if (!user) {
        throw new NotFoundError('Account not found');
      }
      return { statusCode: 200, data: user };
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new AxiosError(
          error.response?.data?.message || 'Error getting user',
          error.code,
          error.config,
          error.request,
          error.response,
        );
      }
      if (error instanceof HttpError) throw keepDeliberateWording(error);
      this.logger.error('Looking the user up by id failed', { error });
      throw markClientSafe(
        new InternalServerError(serverFailureMessage('look up the account')),
      );
    }
  }

  async updateUser(userId: string, userInfo: any, authServiceToken: string) {
    try {
      const config = {
        method: 'put',
        url: `${this.authConfig.iamBackend}/api/v1/users/${userId}`,
        headers: {
          Authorization: `Bearer ${authServiceToken}`,
          'Content-Type': 'application/json',
        },
        data: { ...userInfo },
      };

      const response = await axios(config);

      const user = response.data;
      if (!user) {
        throw new NotFoundError('Account not found');
      }
      return { statusCode: 200, data: user };
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new AxiosError(
          error.response?.data?.message || 'Error updating user',
          error.code,
          error.config,
          error.request,
          error.response,
        );
      }
      if (error instanceof HttpError) throw keepDeliberateWording(error);
      this.logger.error('Updating the user failed', { error });
      throw markClientSafe(
        new InternalServerError(serverFailureMessage('update the account')),
      );
    }
  }

  async checkAdminUser(userId: string, authServiceToken: string) {
    try {
      const config = {
        method: 'get',
        // Internal S2S path: USER_LOOKUP scoped token (no user-session role claim).
        url: `${this.authConfig.iamBackend}/api/v1/users/internal/${userId}/adminCheck`,
        headers: {
          Authorization: `Bearer ${authServiceToken}`,
          'Content-Type': 'application/json',
        },
      };
      const response = await axios(config);
      if (response.status !== 200) {
        throw new InternalServerError('Unexpected error occurred');
      }
      return { statusCode: response.status, data: response.data };
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new AxiosError(
          error.response?.data?.message || 'Error checking adminuser',
          error.code,
          error.config,
          error.request,
          error.response,
        );
      }
      if (error instanceof HttpError) throw keepDeliberateWording(error);
      this.logger.error('Checking whether the user is an admin failed', { error });
      throw markClientSafe(
        new InternalServerError(serverFailureMessage('check the admin account')),
      );
    }
  }
}
