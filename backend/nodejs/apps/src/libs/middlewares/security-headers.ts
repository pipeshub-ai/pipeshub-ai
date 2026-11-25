import { createHash } from 'crypto';
import { readFileSync, statSync } from 'fs';
import { RequestHandler, Response } from 'express';
import helmet from 'helmet';
import { isUnframeablePage } from './no-framing.middleware';
import { hardenedProfile } from '../utils/security-profile';

/**
 * Browser security headers, on for every response (ADR-WEB-01, SEC-D-07). The
 * SPA shells get a document policy whose script-src lists the SHA-256 of each
 * inline script in that very file, so the Next.js static export runs and no
 * other inline script (an XSS in rendered output) can.
 */

export const CSP_REPORT_PATH = '/api/v1/csp-report';
const REPORT_GROUP = 'csp';

export interface CspSources {
  scriptSrc: string[];
  connectSrc: string[];
  frameSrc: string[];
}

export interface SecurityHeaderPolicy {
  /** Origins allowed to frame the app (CSP_FRAME_ANCESTORS); empty: nobody. Never applies to sign-in pages. */
  frameAncestors: string[];
  /** An edition's pages that are never framed either, whatever CSP_FRAME_ANCESTORS says. */
  unframeablePaths: string[];
  /** CSP_REPORT_ONLY=true: report document and API policies without enforcing them; framing stays enforced. */
  reportOnly: boolean;
  /** FRONTEND_PUBLIC_URL is https: send HSTS even when a TLS proxy in front is not trusted to say so. */
  publicUrlIsHttps: boolean;
  /**
   * The hardened header set (ADR-WEB-01): the hardened profile, or STRICT_MODE=true, the opt-in a release without
   * ADR-WEB-01 had. Off (the compatibility profile, ADR-OPS-07): any site may frame the app (sign-in and consent
   * pages aside) unless CSP_FRAME_ANCESTORS names the ones that may, the policies report instead of enforcing unless CSP_REPORT_ONLY
   * says otherwise, and no HSTS, as that release sent.
   */
  enforced: boolean;
  sources: CspSources;
}

// Third parties the shipped UI loads: Google and Microsoft sign-in, Cloudflare's beacon when the
// install sits behind Cloudflare, and GitHub's read-only API for the sign-in page's repository card
// (frontend/lib/branding/defaults.ts githubApiUrl). Anything else is the operator's to add (CSP_*_SRCS) or an edition's.
const BUILT_IN_SOURCES: CspSources = {
  scriptSrc: [
    'https://accounts.google.com',
    'https://static.cloudflareinsights.com',
  ],
  connectSrc: [
    'https://static.cloudflareinsights.com',
    'https://accounts.google.com',
    'https://www.googleapis.com',
    'https://login.microsoftonline.com',
    'https://graph.microsoft.com',
    'https://api.github.com',
  ],
  frameSrc: [
    'https://accounts.google.com',
    'https://login.microsoftonline.com',
  ],
};

const HOST_SOURCE =
  /^(?:https?|wss?):\/\/(?:\*\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*(?::\d{1,5})?(?:\/[^\s;,'"]*)?$/i;

/**
 * Only host sources: an operator value cannot smuggle a keyword ('unsafe-inline',
 * *), a scheme-wide source (https:, data:) or a second directive (;).
 */
export function parseSourceList(raw: string | undefined): string[] {
  return (raw ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter((s) => HOST_SOURCE.test(s));
}

function originOf(url: string | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:'
      ? parsed.origin
      : undefined;
  } catch {
    return undefined;
  }
}

export function securityHeaderPolicy(
  env: NodeJS.ProcessEnv,
  edition: CspSources,
  connectorPublicUrl?: string,
  editionUnframeablePaths: readonly string[] = [],
): SecurityHeaderPolicy {
  const connector = originOf(connectorPublicUrl);
  const unique = (xs: (string | undefined)[]): string[] => [
    ...new Set(xs.filter((x): x is string => !!x)),
  ];
  const enforced = strictHeaders(env);
  const reportOnlyRaw = (env.CSP_REPORT_ONLY ?? '').trim().toLowerCase();
  return {
    frameAncestors: parseSourceList(env.CSP_FRAME_ANCESTORS),
    unframeablePaths: [...editionUnframeablePaths],
    reportOnly: reportOnlyRaw === '' ? !enforced : reportOnlyRaw === 'true',
    enforced,
    publicUrlIsHttps:
      originOf(env.FRONTEND_PUBLIC_URL)?.startsWith('https://') ?? false,
    sources: {
      scriptSrc: unique([
        ...BUILT_IN_SOURCES.scriptSrc,
        ...edition.scriptSrc,
        ...parseSourceList(env.CSP_SCRIPT_SRCS),
      ]),
      connectSrc: unique([
        ...BUILT_IN_SOURCES.connectSrc,
        ...edition.connectSrc,
        ...parseSourceList(env.CSP_CONNECT_SRCS),
        connector,
      ]),
      frameSrc: unique([
        ...BUILT_IN_SOURCES.frameSrc,
        ...edition.frameSrc,
        ...parseSourceList(env.CSP_FRAME_SRCS),
      ]),
    },
  };
}

/** Whether the hardened header set applies: the hardened profile, or upstream's own opt-in, STRICT_MODE=true. */
export function strictHeaders(env: NodeJS.ProcessEnv): boolean {
  return (
    hardenedProfile(env) ||
    (env.STRICT_MODE ?? '').trim().toLowerCase() === 'true'
  );
}

/**
 * The frame-ancestors source list; null where any site may frame the page. Sign-in and consent pages are never
 * framed, as a release without ADR-WEB-01 already refused; on the compatibility profile with no allowlist every other
 * page may be framed, as that release allowed.
 */
function frameAncestors(
  policy: SecurityHeaderPolicy,
  path: string,
): string | null {
  if (isUnframeablePage(path, policy.unframeablePaths)) return "'none'";
  if (policy.frameAncestors.length > 0) return policy.frameAncestors.join(' ');
  return policy.enforced ? "'none'" : null;
}

const framing = (
  policy: SecurityHeaderPolicy,
  path: string,
): [string, string[]][] => {
  const ancestors = frameAncestors(policy, path);
  return ancestors === null ? [] : [['frame-ancestors', [ancestors]]];
};

const serialize = (directives: [string, string[]][]): string =>
  directives.map(([name, values]) => `${name} ${values.join(' ')}`).join('; ');

const reporting: [string, string[]][] = [
  ['report-uri', [CSP_REPORT_PATH]],
  ['report-to', [REPORT_GROUP]],
];

/** Everything that is not a page: JSON, files, streams. Nothing in it may run or load. */
export function apiCsp(policy: SecurityHeaderPolicy, path: string): string {
  return serialize([
    ['default-src', ["'none'"]],
    ['base-uri', ["'none'"]],
    ['form-action', ["'none'"]],
    ...framing(policy, path),
    ...reporting,
  ]);
}

export function documentCsp(
  policy: SecurityHeaderPolicy,
  path: string,
  scriptHashes: string[],
): string {
  const { sources } = policy;
  return serialize([
    ['default-src', ["'self'"]],
    // 'wasm-unsafe-eval' compiles WebAssembly (the Lottie player) and allows no JavaScript eval.
    [
      'script-src',
      ["'self'", "'wasm-unsafe-eval'", ...scriptHashes, ...sources.scriptSrc],
    ],
    // React and Radix set style attributes, and the static export carries <style> blocks.
    ['style-src', ["'self'", "'unsafe-inline'", 'https://accounts.google.com']],
    // A model's or a document's remote image loads only after the user asks for it (SafeMarkdownImage).
    ['img-src', ["'self'", 'data:', 'blob:', 'https:']],
    ['font-src', ["'self'", 'data:']],
    // File previews read the downloaded file back through fetch() on a blob: URL the page created itself.
    ['connect-src', ["'self'", 'blob:', ...sources.connectSrc]],
    // Previews: PDF.js in blob: frames; untrusted HTML runs in a sandboxed srcdoc frame with its own policy.
    ['frame-src', ["'self'", 'blob:', ...sources.frameSrc]],
    ['worker-src', ["'self'", 'blob:']],
    ['media-src', ["'self'", 'blob:', 'data:']],
    ['manifest-src', ["'self'"]],
    ['object-src', ["'none'"]],
    ['base-uri', ["'self'"]],
    // Answers render model output; a form in one must not post off-origin.
    ['form-action', ["'self'"]],
    ...framing(policy, path),
    ...reporting,
  ]);
}

/**
 * The API reference page: a fixed HTML string of our own, with inline scripts and
 * handlers, Google Fonts, and a "try it" console that may call any instance.
 */
export function apiDocsCsp(policy: SecurityHeaderPolicy, path: string): string {
  return serialize([
    ['default-src', ["'self'"]],
    ['script-src', ["'self'", "'unsafe-inline'"]],
    [
      'style-src',
      ["'self'", "'unsafe-inline'", 'https://fonts.googleapis.com'],
    ],
    ['font-src', ["'self'", 'data:', 'https://fonts.gstatic.com']],
    ['img-src', ["'self'", 'data:']],
    ['connect-src', ["'self'", 'https:', 'http:']],
    ['object-src', ["'none'"]],
    ['base-uri', ["'self'"]],
    ['form-action', ["'self'"]],
    ...framing(policy, path),
    ...reporting,
  ]);
}

// A script element ends at `</script` followed by whitespace, `/` or `>`, whatever comes before its `>`, as the
// browser's tokenizer reads it: a narrower end tag would fold the next script into this one's hash.
const INLINE_SCRIPT = /<script\b([^>]*)>([\s\S]*?)<\/script(?=[\s/>])[^>]*>/gi;

/** 'sha256-…' of each inline script's text, as the browser hashes it. */
export function inlineScriptHashes(html: string): string[] {
  const hashes = new Set<string>();
  for (const [, attributes, body] of html.matchAll(INLINE_SCRIPT)) {
    if (/\bsrc\s*=/i.test(attributes ?? '') || !body) continue;
    hashes.add(
      `'sha256-${createHash('sha256').update(body, 'utf8').digest('base64')}'`,
    );
  }
  return [...hashes];
}

/** Memoised per file and version: shells are replaced only by a deploy, which restarts the process. */
export class ShellScriptHashes {
  private readonly cache = new Map<
    string,
    { version: string; hashes: string[] }
  >();

  of(filePath: string): string[] {
    let version: string;
    try {
      const st = statSync(filePath);
      version = `${st.mtimeMs}:${st.size}`;
    } catch {
      return [];
    }
    const hit = this.cache.get(filePath);
    if (hit?.version === version) return hit.hashes;
    const hashes = inlineScriptHashes(readFileSync(filePath, 'utf8'));
    this.cache.set(filePath, { version, hashes });
    return hashes;
  }
}

function setCsp(
  res: Response,
  policy: SecurityHeaderPolicy,
  path: string,
  value: string,
): void {
  if (policy.reportOnly) {
    res.setHeader('Content-Security-Policy-Report-Only', value);
    // frame-ancestors is ignored in a report-only policy, so framing stays enforced on its own.
    const ancestors = frameAncestors(policy, path);
    if (ancestors !== null) {
      res.setHeader('Content-Security-Policy', `frame-ancestors ${ancestors}`);
    }
  } else {
    res.setHeader('Content-Security-Policy', value);
  }
}

/** Replaces the API policy on a response that serves one of the SPA's HTML files; `path` is the request's. */
export function applyDocumentCsp(
  res: Response,
  policy: SecurityHeaderPolicy,
  hashes: ShellScriptHashes,
  htmlFile: string,
  path: string,
): void {
  setCsp(res, policy, path, documentCsp(policy, path, hashes.of(htmlFile)));
}

export function applyApiDocsCsp(policy: SecurityHeaderPolicy): RequestHandler {
  return (req, res, next) => {
    setCsp(res, policy, req.path, apiDocsCsp(policy, req.path));
    next();
  };
}

const PERMISSIONS_POLICY = [
  'camera=()',
  // Dictation in the chat box.
  'microphone=(self)',
  'geolocation=()',
  'payment=()',
  'usb=()',
  'display-capture=()',
].join(', ');

const HSTS = 'max-age=31536000';

export function securityHeaders(
  policy: SecurityHeaderPolicy,
): RequestHandler[] {
  return [
    helmet({
      contentSecurityPolicy: false,
      strictTransportSecurity: false,
      // The MSAL sign-in popup reports back to its opener.
      crossOriginOpenerPolicy: false,
      // The desktop app (file://) loads this server's images and files.
      crossOriginResourcePolicy: false,
      crossOriginEmbedderPolicy: false,
      xFrameOptions: false,
      referrerPolicy: { policy: 'strict-origin-when-cross-origin' },
    }),
    (req, res, next) => {
      res.setHeader('Permissions-Policy', PERMISSIONS_POLICY);
      res.setHeader(
        'Reporting-Endpoints',
        `${REPORT_GROUP}="${CSP_REPORT_PATH}"`,
      );
      // X-Frame-Options cannot name origins, so an embed allowlist leaves framing to the CSP.
      if (frameAncestors(policy, req.path) === "'none'")
        res.setHeader('X-Frame-Options', 'DENY');
      // Browsers ignore HSTS on plain HTTP, so this only ever pins an origin that already served HTTPS.
      if (policy.enforced && (req.secure || policy.publicUrlIsHttps)) {
        res.setHeader('Strict-Transport-Security', HSTS);
      }
      setCsp(res, policy, req.path, apiCsp(policy, req.path));
      next();
    },
  ];
}
