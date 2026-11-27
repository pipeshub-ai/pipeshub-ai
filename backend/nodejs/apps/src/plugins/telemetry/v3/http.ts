import axios from 'axios';
import type * as https from 'https';
import type { V3HttpPort, V3Response } from './sender';

/**
 * The v3 sender's HTTP port on axios. Every status is an answer, not an
 * error; only "no answer at all" is `undefined`. Nothing here logs: the
 * request carries the install's credential.
 */
export function axiosV3Http(options: {
  timeoutMs: number;
  httpsAgent?: () => https.Agent;
  headers?: () => Record<string, string>;
}): V3HttpPort {
  return {
    async post(url, body, headers): Promise<V3Response | undefined> {
      try {
        const response = await axios.post(url, body, {
          headers: {
            'Content-Type': 'application/json',
            ...(options.headers?.() ?? {}),
            ...headers,
          },
          timeout: options.timeoutMs,
          httpsAgent: options.httpsAgent?.(),
          // A redirect is not a v3 answer, and must not carry the credential elsewhere.
          maxRedirects: 0,
          validateStatus: () => true,
        });
        const answered =
          (response.headers as Record<string, unknown> | undefined) ?? {};
        const contract = answered['x-usage-contract'];
        const retryAfter = Number(answered['retry-after']);
        return {
          status: response.status,
          contract: typeof contract === 'string' ? contract : undefined,
          retryAfterSeconds:
            Number.isFinite(retryAfter) && retryAfter > 0
              ? retryAfter
              : undefined,
          body: response.data as unknown,
        };
      } catch {
        return undefined;
      }
    },
  };
}
