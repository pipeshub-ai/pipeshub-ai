import type { Request } from 'express';
import { requestLocale } from '../domain/output-locale';

/** G-52: the person's locale as a request names it (`Accept-Language`), for admission; none when it names none. */
export function localeOf(req: Pick<Request, 'headers'>): { locale?: string } {
  // Hand-built requests (tests, internal callers) may carry no headers at all.
  const headers = req.headers as Request['headers'] | undefined;
  const locale = requestLocale(headers?.['accept-language']);
  return locale === undefined ? {} : { locale };
}
