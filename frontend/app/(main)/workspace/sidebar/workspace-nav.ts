import type { ReactNode } from 'react';
import type { FeatureFlagGate } from '@/lib/store/feature-flags-store';

export interface NavItem {
  icon: string;
  labelKey: string;
  route: string;
  adminOnly?: boolean;
  customIcon?: ReactNode;
  /** Only rendered when the named feature flag is enabled. */
  requiresFlag?: FeatureFlagGate;
  /** Yours rather than the workspace's: an admin finds it in Personal settings only. */
  personal?: boolean;
  /** Renders a Beta badge as the item's rightSlot. */
  beta?: boolean;
}

/** The admin-only lists of Workspace settings, in the order they are shown. */
export type WorkspaceAdminSection = 'ai' | 'integrations' | 'security' | 'system';

/**
 * The workspace sidebar's lists an edition can add entries to. `workspace` means any
 * admin list: the entry joins the one that holds its `after` route, or System.
 */
export type WorkspaceNavSection = 'overview' | WorkspaceAdminSection | 'workspace' | 'developerSettings' | 'personal';

/**
 * One entry an edition adds to the workspace sidebar, through `workspaceNavExtras`
 * in config.ts. Visibility follows the same rules as a built-in entry
 * (`adminOnly`, `requiresFlag`).
 */
export interface WorkspaceNavExtra extends NavItem {
  section: WorkspaceNavSection;
  /** Placed after the entry with this route; absent, or a route the section lacks: at the end. */
  after?: string;
}

/**
 * A section's built-in entries with the extras for it. An extra whose route is a
 * built-in entry's takes that entry's place, so an edition's page for a route
 * supersedes the built-in one rather than showing twice.
 */
export function mergeWorkspaceNav(
  section: WorkspaceNavSection,
  items: readonly NavItem[],
  extras: readonly WorkspaceNavExtra[],
): NavItem[] {
  const merged: NavItem[] = [...items];
  for (const { section: target, after, ...item } of extras) {
    if (target !== section) continue;
    const same = merged.findIndex((entry) => entry.route === item.route);
    if (same !== -1) {
      merged[same] = item;
      continue;
    }
    const anchor = after === undefined ? -1 : merged.findIndex((entry) => entry.route === after);
    merged.splice(anchor === -1 ? merged.length : anchor + 1, 0, item);
  }
  return merged;
}

/**
 * The extras with each `workspace` one given the admin list it belongs in: the list
 * holding its route (so it still replaces that entry) or its `after` route, else System.
 * The route can be a built-in entry's or an earlier extra's.
 */
export function placeWorkspaceExtras(
  adminItems: Readonly<Record<WorkspaceAdminSection, readonly NavItem[]>>,
  extras: readonly WorkspaceNavExtra[],
): WorkspaceNavExtra[] {
  const adminSections = Object.keys(adminItems) as WorkspaceAdminSection[];
  const sectionByRoute = new Map<string, WorkspaceAdminSection>();
  for (const key of adminSections) {
    for (const item of adminItems[key]) sectionByRoute.set(item.route, key);
  }
  const sectionOf = (route: string | undefined) => (route === undefined ? undefined : sectionByRoute.get(route));
  return extras.map((extra) => {
    const section =
      extra.section === 'workspace' ? sectionOf(extra.route) ?? sectionOf(extra.after) ?? 'system' : extra.section;
    if ((adminSections as string[]).includes(section) && !sectionByRoute.has(extra.route)) {
      sectionByRoute.set(extra.route, section as WorkspaceAdminSection);
    }
    return section === extra.section ? extra : { ...extra, section };
  });
}
