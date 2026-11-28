/** Settings split in two: your own, and the workspace's, which only admins open. */
export type SettingsScope = 'personal' | 'workspace';

const PERSONAL_ROUTES = [
  '/workspace/profile',
  '/workspace/ai-accounts',
  '/workspace/schedules',
  '/workspace/memories',
  '/workspace/connectors/personal',
  '/workspace/actions/personal',
  '/workspace/mcp-servers/personal',
  '/workspace/skills/personal',
  '/workspace/archived-chats',
  '/workspace/developer-settings/personal-access-tokens',
];

/** Members have one settings list, so every page they can open belongs to their personal settings. */
export function settingsScopeFor(
  pathname: string,
  isAdmin: boolean | null,
  extraPersonalRoutes: readonly string[] = [],
): SettingsScope {
  if (isAdmin !== true) return 'personal';
  const isPersonal = [...PERSONAL_ROUTES, ...extraPersonalRoutes].some(
    (route) => pathname === route || pathname.startsWith(`${route}/`),
  );
  return isPersonal ? 'personal' : 'workspace';
}
