import type { APIRequestContext, APIResponse } from '@playwright/test';

const MAX_RETRIES = 3;

export async function postWithRetry(
  apiContext: APIRequestContext,
  url: string,
  data: unknown,
): Promise<APIResponse> {
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    const response = await apiContext.post(url, { data });

    if (response.status() !== 429) return response;

    const body = await response.json().catch(() => ({}));
    const retryAfter = (body as { error?: { retryAfter?: number } }).error?.retryAfter ?? 5;
    await new Promise((r) => setTimeout(r, retryAfter * 1_000));
  }

  return apiContext.post(url, { data });
}
