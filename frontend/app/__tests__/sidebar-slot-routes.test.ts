import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

// app/(main)/layout renders the @sidebar parallel slot beside every page. A page
// with no matching slot entry still loads on a full reload (default.tsx), but a
// client-side link to it never navigates: the sidebar item looks dead.
const MAIN = path.resolve(__dirname, '../(main)');
// Reached only by a full page load (window.location / a typed URL), never a client link.
const FULL_LOAD_ONLY = new Set(['record', 'workspace/groups/permissions']);
// Upstream slots whose page moved or was never added; kept so this tree does not diverge from main.
const UPSTREAM_SLOTS_WITHOUT_PAGE = new Set([
  'workspace-settings',
  'workspace/api-toolsets',
  'workspace/mcp-servers',
]);

function pageRoutes(dir: string, prefix = ''): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    if (entry.isFile()) return entry.name === 'page.tsx' ? [prefix] : [];
    if (!entry.isDirectory() || entry.name.startsWith('@') || entry.name === '__tests__') return [];
    return pageRoutes(path.join(dir, entry.name), prefix ? `${prefix}/${entry.name}` : entry.name);
  });
}

const SIDEBAR = path.join(MAIN, '@sidebar');

describe('the @sidebar slot', () => {
  it('has an entry for every page a link can open', () => {
    const missing = pageRoutes(MAIN).filter(
      (route) =>
        !FULL_LOAD_ONLY.has(route) &&
        !fs.existsSync(path.join(MAIN, '@sidebar', route, 'page.tsx')),
    );
    expect(missing).toEqual([]);
  });

  // A slot left behind when its page is removed keeps a dead route name in the tree.
  it('has no entry for a page that does not exist', () => {
    const orphans = pageRoutes(SIDEBAR).filter(
      (route) =>
        route !== '' &&
        !UPSTREAM_SLOTS_WITHOUT_PAGE.has(route) &&
        !fs.existsSync(path.join(MAIN, route, 'page.tsx')),
    );
    expect(orphans).toEqual([]);
  });
});
