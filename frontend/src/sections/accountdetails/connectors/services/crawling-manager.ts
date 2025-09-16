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
  static async schedule(connector: string, payload: CustomSchedulePayload): Promise<void> {
    await axios.post(`${BASE_URL}/${connector}/schedule`, payload);
  }

  static async remove(connector: string): Promise<void> {
    await axios.delete(`${BASE_URL}/${connector}/schedule`);
  }
}


