import express, { Router } from 'express';
import { Logger } from '../services/logger.service';
import { CSP_REPORT_PATH } from './security-headers';

const MAX_LOGGED_PER_MINUTE = 30;
const FIELD_MAX = 200;

interface Violation {
  directive: string;
  blocked: string;
  page: string;
  disposition: string;
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.slice(0, FIELD_MAX) : '';
}

/** Only an origin or a keyword (inline, eval): a blocked URL's path and query can carry a token. */
function originOnly(value: unknown): string {
  const raw = text(value);
  try {
    return new URL(raw).origin;
  } catch {
    return /^[a-z-]+$/i.test(raw) ? raw : '';
  }
}

function pathOnly(value: unknown): string {
  try {
    return new URL(text(value)).pathname;
  } catch {
    return '';
  }
}

/** The legacy `report-uri` body (`{"csp-report": …}`) and the Reporting API's array of `csp-violation` reports. */
export function violationsOf(body: unknown): Violation[] {
  const entries: Record<string, unknown>[] = [];
  if (Array.isArray(body)) {
    for (const report of body.slice(0, 10)) {
      if (
        report &&
        typeof report === 'object' &&
        (report as { type?: unknown }).type === 'csp-violation'
      ) {
        const inner = (report as { body?: unknown }).body;
        if (inner && typeof inner === 'object')
          entries.push(inner as Record<string, unknown>);
      }
    }
  } else if (body && typeof body === 'object') {
    const legacy = (body as Record<string, unknown>)['csp-report'];
    if (legacy && typeof legacy === 'object')
      entries.push(legacy as Record<string, unknown>);
  }
  return entries.map((e) => ({
    directive: text(
      e['effective-directive'] ??
        e.effectiveDirective ??
        e['violated-directive'],
    ),
    blocked: originOnly(e['blocked-uri'] ?? e.blockedURL),
    page: pathOnly(e['document-uri'] ?? e.documentURL),
    disposition: text(e.disposition),
  }));
}

/**
 * Where browsers send CSP violations (ADR-WEB-01). Unauthenticated by nature, so
 * the body is small, every field is cut to an origin or a path, and the log is
 * capped per minute: a flood costs one warning line.
 */
export function createCspReportRouter(
  logger: Logger,
  now: () => number = Date.now,
): Router {
  const router = Router();
  let windowStart = 0;
  let logged = 0;
  let dropped = 0;
  router.post(
    CSP_REPORT_PATH,
    express.json({
      type: [
        'application/csp-report',
        'application/reports+json',
        'application/json',
      ],
      limit: '16kb',
    }),
    (req, res) => {
      const t = now();
      if (t - windowStart >= 60_000) {
        if (dropped > 0) logger.warn('csp.violation.dropped', { dropped });
        windowStart = t;
        logged = 0;
        dropped = 0;
      }
      for (const violation of violationsOf(req.body)) {
        if (logged < MAX_LOGGED_PER_MINUTE) {
          logged += 1;
          logger.warn('csp.violation', violation);
        } else {
          dropped += 1;
        }
      }
      res.status(204).end();
    },
  );
  return router;
}
