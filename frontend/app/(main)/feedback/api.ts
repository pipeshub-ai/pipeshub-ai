import { apiClient } from '@/lib/api';
import type { CreateFeedbackResponse, FeedbackKind } from './types';

const BASE_URL = '/api/v1/feedback';

export const FeedbackApi = {
  async getSmtpStatus(): Promise<{ configured: boolean }> {
    const { data } = await apiClient.get<{ configured: boolean }>(`${BASE_URL}/smtp-status`);
    return data;
  },

  async submit(payload: {
    kind: FeedbackKind;
    description: string;
    files: File[];
  }): Promise<CreateFeedbackResponse> {
    const form = new FormData();
    form.append('kind', payload.kind);
    form.append('description', payload.description);
    for (const file of payload.files) {
      form.append('attachments', file);
    }

    const { data } = await apiClient.post<CreateFeedbackResponse>(BASE_URL, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return data;
  },
};
