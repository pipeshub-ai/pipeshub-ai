/**
 * The workspace sidebar's extension point: entries an edition adds through
 * `workspaceNavExtras` in config.ts, merged into the built-in sections.
 */
import { describe, expect, it } from 'vitest';
import { mergeWorkspaceNav, type NavItem, type WorkspaceNavExtra } from '../workspace-nav';
import { workspaceNavExtras } from '../workspace-nav-extras';

const item = (route: string, labelKey = `label.${route}`): NavItem => ({ icon: 'circle', labelKey, route });
const BUILT_IN = [item('/workspace/a'), item('/workspace/b'), item('/workspace/c')];
const routes = (items: NavItem[]) => items.map((entry) => entry.route);

describe('mergeWorkspaceNav', () => {
  it('the default adds no entry: every section is its built-in list', () => {
    expect(workspaceNavExtras).toEqual([]);
    for (const section of ['overview', 'security', 'workspace', 'developerSettings', 'personal'] as const) {
      expect(mergeWorkspaceNav(section, BUILT_IN, workspaceNavExtras)).toEqual(BUILT_IN);
    }
  });

  it('appends an extra to its own section only', () => {
    const extras: WorkspaceNavExtra[] = [{ ...item('/workspace/x'), section: 'workspace' }];
    expect(routes(mergeWorkspaceNav('workspace', BUILT_IN, extras))).toEqual([
      '/workspace/a', '/workspace/b', '/workspace/c', '/workspace/x',
    ]);
    expect(mergeWorkspaceNav('personal', BUILT_IN, extras)).toEqual(BUILT_IN);
  });

  it('places an extra after the entry it names, and at the end when that entry is missing', () => {
    const extras: WorkspaceNavExtra[] = [
      { ...item('/workspace/x'), section: 'workspace', after: '/workspace/a' },
      { ...item('/workspace/y'), section: 'workspace', after: '/workspace/missing' },
    ];
    expect(routes(mergeWorkspaceNav('workspace', BUILT_IN, extras))).toEqual([
      '/workspace/a', '/workspace/x', '/workspace/b', '/workspace/c', '/workspace/y',
    ]);
  });

  it('an extra for a built-in route takes its place instead of showing twice', () => {
    const extras: WorkspaceNavExtra[] = [{ ...item('/workspace/b', 'label.replaced'), section: 'workspace' }];
    const merged = mergeWorkspaceNav('workspace', BUILT_IN, extras);
    expect(routes(merged)).toEqual(['/workspace/a', '/workspace/b', '/workspace/c']);
    expect(merged[1]).toEqual(item('/workspace/b', 'label.replaced'));
    expect(merged[1]).not.toHaveProperty('section');
  });

  it('leaves the built-in list as it was', () => {
    const before = [...BUILT_IN];
    mergeWorkspaceNav('workspace', BUILT_IN, [{ ...item('/workspace/x'), section: 'workspace' }]);
    expect(BUILT_IN).toEqual(before);
  });
});
