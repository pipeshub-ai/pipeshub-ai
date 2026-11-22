import { RequestHandler } from 'express';

/** Pages that must never render inside another site's frame. */
export const UNFRAMEABLE_PAGE_PATHS = ['/login', '/sign-up', '/oauth'];

/**
 * Forbids framing, so a page cannot be overlaid and clickjacked, e.g. a consent page tricked
 * into "Allow". The CSP is appended as its own header: browsers enforce every policy they
 * get, so any policy already set (helmet's, in strict mode) still applies.
 */
export const noFraming: RequestHandler = (_req, res, next) => {
  res.setHeader('X-Frame-Options', 'DENY');
  res.append('Content-Security-Policy', "frame-ancestors 'none'");
  next();
};
