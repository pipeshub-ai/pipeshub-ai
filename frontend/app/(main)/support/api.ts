'use client';

import { apiClient } from '@/lib/api';
import type { RecentError } from '@/lib/support/recent-errors';
import type { SupportAreaKey, SupportContext, SupportRequestResult } from './types';

const BASE_URL = '/api/v1/support';

export interface SupportRequestPayload {
  area: SupportAreaKey;
  description: string;
  requestKey: string;
  recentErrors: RecentError[];
  files: File[];
}

// The dialog shows its own messages, so the global error toast stays quiet for these calls.
const quiet = { suppressErrorToast: true } as const;

export const SupportApi = {
  async getContext(signal?: AbortSignal): Promise<SupportContext> {
    const { data } = await apiClient.get<SupportContext>(`${BASE_URL}/context`, { signal, ...quiet });
    return data;
  },

  async submit(payload: SupportRequestPayload): Promise<SupportRequestResult> {
    const form = new FormData();
    form.append('area', payload.area);
    form.append('description', payload.description);
    form.append('requestKey', payload.requestKey);
    if (payload.recentErrors.length > 0) {
      form.append('recentErrors', JSON.stringify(payload.recentErrors));
    }
    for (const file of payload.files) {
      form.append('attachments', file);
    }
    const { data } = await apiClient.post<SupportRequestResult>(BASE_URL, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      ...quiet,
    });
    return data;
  },
};
