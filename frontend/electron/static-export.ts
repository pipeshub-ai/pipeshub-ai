import * as fs from 'fs';
import * as path from 'path';

/**
 * Maps an app:// pathname to a file in the Next.js static export.
 *
 * `/record/<recordId>` cannot be pre-rendered per id under `output: 'export'`,
 * so the build emits one `/record/` shell that reads the id from
 * `window.location.pathname`. The web build gets this from the backend SPA
 * fallback (backend/nodejs/apps/src/app.ts) and from next.config rewrites in
 * dev; the desktop app serves the export itself, so it needs the same mapping.
 */
const RECORD_ROUTE = /^\/record\/[^/]+(?:\/(.*))?$/;

/** Null when the decoded pathname (e.g. an encoded `..%2f`) escapes the export. */
export function resolveStaticExportPath(
  staticDir: string,
  pathname: string,
  exists: (filePath: string) => boolean = fs.existsSync,
): string | null {
  const resolved = resolveWithinExport(staticDir, pathname, exists);
  const relative = path.relative(staticDir, resolved);
  return relative.startsWith('..') || path.isAbsolute(relative) ? null : resolved;
}

function resolveWithinExport(
  staticDir: string,
  pathname: string,
  exists: (filePath: string) => boolean,
): string {
  const record = RECORD_ROUTE.exec(pathname);
  if (record) {
    // Keep the file name for route payloads the router fetches on client-side
    // navigation (e.g. /record/<id>/index.txt -> record/index.txt).
    const rest = record[1] ?? '';
    const file = path.extname(rest) ? path.basename(rest) : 'index.html';
    return path.join(staticDir, 'record', file);
  }

  const filePath = path.join(staticDir, pathname);
  // An extensionless route resolves to the directory the export wrote it as
  // (/login -> out/login), whether or not it carries a trailing slash.
  // Fetching the directory itself fails with ERR_UNEXPECTED, so prefer the
  // index.html inside it -- this is what a full navigation to a route hits.
  if (!path.extname(filePath)) {
    const withIndex = path.join(filePath, 'index.html');
    if (exists(withIndex)) return withIndex;
  }
  return filePath;
}
