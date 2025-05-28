import axios, { AxiosError } from 'axios';
import { injectable } from 'inversify';
import { InternalServerError } from '../../../libs/errors/http.errors';
import { generateFetchConfigAuthToken } from '../utils/generateAuthToken';

interface ConfigManagerResponse {
  statusCode: number;
  data: any;
}

export const GOOGLE_AUTH_CONFIG_PATH =
  'api/v1/configurationManager/internal/authConfig/google';
export const AZURE_AD_AUTH_CONFIG_PATH =
  'api/v1/configurationManager/internal/authConfig/azureAd';
export const MICROSOFT_AUTH_CONFIG_PATH =
  'api/v1/configurationManager/internal/authConfig/microsoft';
export const OAUTH_AUTH_CONFIG_PATH =
  'api/v1/configurationManager/internal/authConfig/oauth';

@injectable()
export class ConfigurationManagerService {
  constructor() {}

  async getConfig(
    cmBackendUrl: string,
    configUrlPath: string,
    user: Record<string, any>,
    scopedJwtSecret: string,
  ): Promise<ConfigManagerResponse> {
    try {
      const config = {
        method: 'get' as const,
        url: `${cmBackendUrl}/${configUrlPath}`,
        headers: {
          Authorization: `Bearer ${await generateFetchConfigAuthToken(user, scopedJwtSecret)}`,
          'Content-Type': 'application/json',
        },
      };

      const response = await axios(config);
      return { statusCode: 200, data: response.data };
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new AxiosError(
          error.response?.data?.message || 'Error getting config',
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
