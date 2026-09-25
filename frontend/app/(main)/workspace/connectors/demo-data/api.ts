import { apiClient } from '@/lib/api';

const BASE_URL = '/api/v1/knowledgeBase/demo-data';

/** Whether the Acme Corp demo data reaches this person's answers, search and listings. */
export interface DemoDataStatus {
  hasDemo: boolean;
  include: boolean;
  /** Their own choice; null while they follow the default. */
  chosen: boolean | null;
  realData: boolean;
  demoConnectorIds: string[];
}

export const DemoDataApi = {
  async getStatus(): Promise<DemoDataStatus> {
    // Background read on the chat landing: a failure only leaves things as they were.
    const { data } = await apiClient.get<DemoDataStatus>(`${BASE_URL}/status`, { suppressErrorToast: true });
    return data;
  },

  /** `null` goes back to the default. */
  async setInclude(include: boolean | null): Promise<DemoDataStatus> {
    const { data } = await apiClient.put<DemoDataStatus>(`${BASE_URL}/preference`, { include });
    return data;
  },
};
