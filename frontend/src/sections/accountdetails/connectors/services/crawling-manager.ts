import axios from 'src/utils/axios';

const BASE_URL = '/api/v1/crawlingManager';

export type CustomSchedulePayload = {
  scheduleConfig: {
    scheduleType: string;
    isEnabled: boolean;
    timezone: string;
    cronExpression: string;
  };
  priority?: number;
  maxRetries?: number;
  timeout?: number;
};

export class CrawlingManagerApi {
  static async schedule(connector: string, connectorId: string, payload: CustomSchedulePayload): Promise<void> {
    await axios.post(`${BASE_URL}/${connector}/${connectorId}/schedule`, payload);
  }

  static async remove(connector: string, connectorId: string): Promise<void> {
    await axios.delete(`${BASE_URL}/${connector}/${connectorId}/remove`);
  }
}


