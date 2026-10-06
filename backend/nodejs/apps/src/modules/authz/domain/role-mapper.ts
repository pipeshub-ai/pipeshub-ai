import { CanonicalRole } from './ladder';

export type MappedResourceType = 'project' | 'chat' | 'kb' | 'agent' | 'team';

type RoleEntries = ReadonlyArray<readonly [string, CanonicalRole]>;

/**
 * Stored values per resource. For `fromCanonical` the first entry for a
 * canonical role wins, so FILEORGANIZER (a KB record-level alias) is listed
 * after WRITER. Team roles describe membership and have no resource mapping (S5).
 */
export const ROLE_TABLE: Readonly<Record<MappedResourceType, RoleEntries>> = {
  project: [
    ['owner', 'owner'],
    ['editor', 'editor'],
    ['viewer', 'viewer'],
  ],
  chat: [
    ['owner', 'owner'],
    ['write', 'editor'],
    ['read', 'viewer'],
  ],
  kb: [
    ['OWNER', 'owner'],
    ['ORGANIZER', 'manager'],
    ['WRITER', 'editor'],
    ['FILEORGANIZER', 'editor'],
    ['COMMENTER', 'commenter'],
    ['READER', 'viewer'],
  ],
  agent: [
    ['OWNER', 'owner'],
    ['ORGANIZER', 'manager'],
    ['WRITER', 'editor'],
    ['READER', 'viewer'],
  ],
  team: [
    ['OWNER', 'owner'],
    ['WRITER', 'editor'],
    ['READER', 'viewer'],
  ],
};

const TO_CANONICAL = new Map<string, Map<string, CanonicalRole>>();
const FROM_CANONICAL = new Map<string, Map<CanonicalRole, string>>();
for (const [type, entries] of Object.entries(ROLE_TABLE)) {
  const forward = new Map<string, CanonicalRole>();
  const reverse = new Map<CanonicalRole, string>();
  for (const [stored, canonical] of entries) {
    forward.set(stored, canonical);
    if (!reverse.has(canonical)) {
      reverse.set(canonical, stored);
    }
  }
  TO_CANONICAL.set(type, forward);
  FROM_CANONICAL.set(type, reverse);
}

/** Unknown resource types and unknown or missing stored values map to `none` (fail closed). */
export function toCanonical(
  resourceType: string,
  stored: string | null | undefined,
): CanonicalRole {
  if (typeof stored !== 'string') {
    return 'none';
  }
  return TO_CANONICAL.get(resourceType)?.get(stored) ?? 'none';
}

/** Returns null when the resource has no stored value for the canonical role. */
export function fromCanonical(
  resourceType: string,
  canonical: CanonicalRole,
): string | null {
  return FROM_CANONICAL.get(resourceType)?.get(canonical) ?? null;
}
