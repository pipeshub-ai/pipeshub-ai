import { Response, NextFunction } from 'express';
import axios, { AxiosRequestConfig, AxiosResponse } from 'axios';
import FormData from 'form-data';

import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadGatewayError,
  BadRequestError,
  ConflictError,
  ForbiddenError,
  HttpError,
  NotFoundError,
  ServiceUnavailableError,
  TooManyRequestsError,
  UnauthorizedError,
  UnprocessableEntityError,
} from '../../../libs/errors/http.errors';
import { serverFailureMessage } from '../../../libs/errors/reader-friendly';
import { AppConfig } from '../../tokens_manager/config/config';

const logger = Logger.getInstance({ service: 'Chat Speech Proxy' });

// Headers that cause issues when blindly forwarded through the node proxy to
// the Python backend (hop-by-hop, content-length mismatches after re-encoding,
// etc.). Matches the set we already strip in other AI proxy paths.
const HOP_BY_HOP_HEADERS = new Set([
  'host',
  'content-length',
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  'accept-encoding',
]);

function buildForwardHeaders(
  req: AuthenticatedUserRequest,
  extra: Record<string, string> = {},
): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(req.headers)) {
    if (!value) continue;
    if (HOP_BY_HOP_HEADERS.has(key.toLowerCase())) continue;
    headers[key] = Array.isArray(value) ? value.join(', ') : String(value);
  }
  // Ensure the auth token is always forwarded even if the iteration above
  // missed it (lowercased Express headers vs mixed-case input).
  if (req.headers.authorization && !headers.authorization) {
    headers.authorization = String(req.headers.authorization);
  }
  return { ...headers, ...extra };
}

/**
 * Shown when one PipesHub service cannot reach another. The fault is on the
 * server, so it never asks the reader to check their own connection, and it
 * never names the service that is down.
 */
const SERVICE_UNAVAILABLE_MESSAGE =
  'PipesHub is having trouble reaching one of its services. Try again in a minute; if it continues, ask your admin to check the services page.';

// The status the speech service chose, kept rather than flattened to one code.
const CLIENT_ERRORS = {
  400: BadRequestError,
  401: UnauthorizedError,
  403: ForbiddenError,
  404: NotFoundError,
  409: ConflictError,
  422: UnprocessableEntityError,
  429: TooManyRequestsError,
} as const;

const readerText = (data: unknown): string | undefined => {
  if (typeof data !== 'object' || data === null) return undefined;
  const body = data as Record<string, unknown>;
  for (const key of ['detail', 'message']) {
    const value = body[key];
    if (typeof value === 'string' && value.length > 0) return value;
  }
  return undefined;
};

/**
 * The body to send on for a speech service reply of 400 or worse.
 *
 * Every route here forwards the upstream status as-is, so this decides only
 * what the reader is told. A 4xx keeps the service's own words — those are
 * written for the person who made the request. A 5xx does not: its words
 * describe the machine that broke, so they go to the log and plain advice
 * goes to the person.
 */
function bodyForUpstreamError(
  status: number,
  data: unknown,
  action: string,
): unknown {
  if (status < 400) return data;
  const ownWords = readerText(data);
  if (status < 500) {
    logger.info(`${action} upstream returned ${status}`, { status });
    return ownWords !== undefined ? { detail: ownWords } : data;
  }
  logger.error(`${action} upstream returned ${status}`, { status, data });
  return { detail: serverFailureMessage(action) };
}

/**
 * Turn a thrown call to the speech service into the error a caller can hand
 * to the error middleware. These routes accept any status without throwing,
 * so this runs for transport failures — an unreachable service, a timeout —
 * and keeps the status meaningful if a response ever does come with one.
 */
function mapAxiosError(error: unknown, action: string): Error {
  const err = error as {
    response?: { status: number; data: unknown };
    code?: string;
    message?: string;
  };
  if (err?.response) {
    const { status, data } = err.response;
    logger.error(`${action} upstream returned ${status}`, { status, data });
    const ownWords = readerText(data);
    if (status >= 400 && status < 500) {
      // Keep the status the service chose: 404 must not read as 502, and an
      // unmapped one (413 for an oversized recording, say) must not read as
      // 400 either, or the caller cannot tell what to do about it. The words
      // are the service's when it wrote any, matching bodyForUpstreamError.
      const message = ownWords ?? serverFailureMessage(action);
      if (status in CLIENT_ERRORS) {
        const ClientError = CLIENT_ERRORS[status as keyof typeof CLIENT_ERRORS];
        return new ClientError(message);
      }
      return new HttpError('UPSTREAM_CLIENT_ERROR', message, status);
    }
    return new BadGatewayError(serverFailureMessage(action));
  }
  logger.error(`${action} upstream unreachable`, {
    code: err?.code,
    message: err?.message,
  });
  return new ServiceUnavailableError(SERVICE_UNAVAILABLE_MESSAGE);
}

/**
 * GET /api/v1/chat/speech/capabilities
 *
 * Proxies to the Python AI backend so the chat UI can discover whether a
 * server-side TTS/STT provider has been configured. The backend returns a
 * small JSON summary; we forward it as-is. The route is intentionally
 * tolerant — upstream failures surface as ServiceUnavailableError so the
 * client falls back to the browser Web Speech API.
 */
export const getSpeechCapabilities =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const url = `${appConfig.aiBackend}/api/v1/chat/speech/capabilities`;
      const response = await axios.get(url, {
        headers: buildForwardHeaders(req),
        timeout: 10_000,
        // We want to forward whatever status the upstream returned (e.g. 409
        // when no provider is configured) rather than throwing here.
        validateStatus: () => true,
      });

      res
        .status(response.status)
        .json(
          bodyForUpstreamError(
            response.status,
            response.data,
            'check the speech settings',
          ),
        );
    } catch (error) {
      next(mapAxiosError(error, 'check the speech settings'));
    }
  };

/**
 * POST /api/v1/chat/speak
 *
 * Forwards the JSON payload `{ text, voice?, format?, speed? }` to the Python
 * AI backend and streams the binary audio response back to the client. The
 * Python route already handles character-limit validation, provider dispatch,
 * and format negotiation — this proxy only needs to shuttle bytes.
 */
export const synthesizeSpeech =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const url = `${appConfig.aiBackend}/api/v1/chat/speak`;
      const requestConfig: AxiosRequestConfig = {
        headers: buildForwardHeaders(req, { 'Content-Type': 'application/json' }),
        // Providers like OpenAI can take 10-30s for longer passages. Match
        // the 120s timeout the frontend hook uses so we don't pre-empt it.
        timeout: 120_000,
        responseType: 'arraybuffer',
        validateStatus: () => true,
      };

      const response: AxiosResponse<Buffer> = await axios.post(
        url,
        req.body ?? {},
        requestConfig,
      );

      // Error paths: upstream returns JSON. Forward it as JSON so the client
      // can show the specific failure reason instead of a binary blob.
      if (response.status >= 400) {
        const payload = parseUpstreamError(response);
        res
          .status(response.status)
          .json(
            bodyForUpstreamError(
              response.status,
              payload,
              'read this message aloud',
            ),
          );
        return;
      }

      const contentType =
        (response.headers['content-type'] as string | undefined) ??
        'application/octet-stream';
      res.status(response.status);
      res.setHeader('Content-Type', contentType);
      res.setHeader('Cache-Control', 'no-store');
      if (response.headers['x-tts-provider']) {
        res.setHeader('X-TTS-Provider', String(response.headers['x-tts-provider']));
      }
      if (response.headers['x-tts-model']) {
        res.setHeader('X-TTS-Model', String(response.headers['x-tts-model']));
      }
      res.send(Buffer.from(response.data));
    } catch (error) {
      next(mapAxiosError(error, 'read this message aloud'));
    }
  };

/**
 * POST /api/v1/chat/transcribe
 *
 * Forwards a multipart upload (parsed by multer into `req.file`) to the
 * Python AI backend and returns its JSON transcript response. The field name
 * must match the Python side (`file`) and the optional `language` form field
 * is preserved when present.
 */
export const transcribeAudio =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const file = (req as AuthenticatedUserRequest & { file?: Express.Multer.File }).file;
      if (!file) {
        res.status(400).json({
          status: 'error',
          message: "Audio file is required (field 'file')",
        });
        return;
      }

      const form = new FormData();
      form.append('file', file.buffer, {
        filename: file.originalname || 'audio',
        contentType: file.mimetype || 'application/octet-stream',
      });
      const language = typeof req.body?.language === 'string' ? req.body.language : undefined;
      if (language) {
        form.append('language', language);
      }

      const url = `${appConfig.aiBackend}/api/v1/chat/transcribe`;
      const headers = buildForwardHeaders(req);
      // Remove the inbound multipart headers — axios + form-data will set the
      // correct Content-Type with a fresh boundary for the outbound request.
      delete headers['content-type'];
      delete headers['Content-Type'];

      const response = await axios.post(url, form, {
        headers: { ...headers, ...form.getHeaders() },
        timeout: 120_000,
        maxBodyLength: Infinity,
        maxContentLength: Infinity,
        validateStatus: () => true,
      });

      res
        .status(response.status)
        .json(
          bodyForUpstreamError(
            response.status,
            response.data,
            'turn your recording into text',
          ),
        );
    } catch (error) {
      next(mapAxiosError(error, 'turn your recording into text'));
    }
  };

function parseUpstreamError(response: AxiosResponse<Buffer>): unknown {
  try {
    const contentType = String(response.headers['content-type'] || '');
    const raw = Buffer.from(response.data).toString('utf-8');
    if (!raw) {
      return { detail: `Upstream returned status ${response.status}` };
    }
    if (contentType.includes('application/json')) {
      return JSON.parse(raw);
    }
    return { detail: raw };
  } catch {
    return { detail: `Upstream returned status ${response.status}` };
  }
}
