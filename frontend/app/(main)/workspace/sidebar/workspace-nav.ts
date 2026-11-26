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
  /** Renders a Beta badge as the item's rightSlot. */
  beta?: boolean;
}

/** The workspace sidebar's lists an edition can add entries to. */
export type WorkspaceNavSection = 'overview' | 'security' | 'workspace' | 'developerSettings' | 'personal';

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
