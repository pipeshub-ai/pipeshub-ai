import axios, { AxiosError } from 'axios';
import { injectable } from 'inversify';
import { InternalServerError } from '../../../libs/errors/http.errors';

interface ConfigManagerResponse {
  statusCode: number;
  data: any;
}
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
      throw new InternalServerError(
        error instanceof Error ? error.message : 'Unexpected error occurred',
      );
    }
  }
}
