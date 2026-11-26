import type {
  ChangeEvent as GeneratedChangeEvent,
  ConfigChanged as GeneratedConfigChanged,
} from '../contracts/generated';

/** `org:o1`, `team:t9`, `agent:a7`, `user:u3` (contracts/control-plane/common.schema.json `ScopeRef`). */
export type ScopeRef = string;
/** `user:u3`, `service:{name}` or `system` (`ActorRef`). */
export type ActorRef = string;

export const actorRefOfUser = (userId: string): ActorRef => `user:${userId}`;
export const orgScope = (orgId: string): ScopeRef => `org:${orgId}`;
export const userScope = (userId: string): ScopeRef => `user:${userId}`;

/** The audit payload of a committed change (`config.changed` on the `cp:{orgId}` stream; config-changed.schema.json). */
export type ConfigChanged = GeneratedConfigChanged;

/** What one control-plane write changed, as its `work` reports it: the payload without who, where and when. */
export type ConfigChange = Omit<
  ConfigChanged,
  'orgId' | 'actor' | 'approvalRef' | 'headRevision' | 'repaired'
>;

export type ConfigObjectRef = ConfigChanged['object'];

/** What caches and running work learn of a commit (change-event.schema.json, topic `control-plane-events`). */
export type ChangeEvent = GeneratedChangeEvent;

export const CONFIG_CHANGED_KIND = 'config.changed';
export const CONTROL_PLANE_WRITER = 'control-plane';
export const configStreamId = (orgId: string): string => `cp:${orgId}`;

export function changeEventOf(changed: ConfigChanged): ChangeEvent {
  return {
    type: 'cp.changed',
    orgId: changed.orgId,
    revision: changed.headRevision,
    previous: changed.headRevision - 1,
    touched: [{ kind: changed.object.type, id: changed.object.id }],
    narrowing: changed.narrowing ?? false,
  };
}

type Json = unknown;

const isRecord = (value: Json): value is Record<string, Json> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

function sameJson(left: Json, right: Json): boolean {
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((item, index) => sameJson(item, right[index]))
    );
  }
  if (isRecord(left) || isRecord(right)) {
    if (!isRecord(left) || !isRecord(right)) return false;
    const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
    return [...keys].every((key) => sameJson(left[key], right[key]));
  }
  return Object.is(left, right);
}

function collectPaths(
  before: Json,
  after: Json,
  prefix: string,
  out: Set<string>,
): void {
  if (sameJson(before, after)) return;
  if (isRecord(before) && isRecord(after)) {
    for (const key of new Set([
      ...Object.keys(before),
      ...Object.keys(after),
    ])) {
      collectPaths(
        before[key],
        after[key],
        prefix === '' ? key : `${prefix}.${key}`,
        out,
      );
    }
    return;
  }
  out.add(prefix === '' ? '$' : prefix);
}

/**
 * The dotted paths at which `after` differs from `before`. Objects are walked
 * key by key (order ignored); arrays and scalars are compared whole, so a
 * reordered list is one path, not one per element. A document created or
 * removed whole reports its top-level fields.
 */
export function changedPaths(before: Json, after: Json): string[] {
  const out = new Set<string>();
  const absent = (value: Json, other: Json): Json =>
    (value === null || value === undefined) && isRecord(other)
      ? {}
      : (value ?? null);
  collectPaths(absent(before, after), absent(after, before), '', out);
  return [...out].sort();
}

/**
 * Joins paths into one field of at most `max` characters, never cutting a
 * path: what does not fit is counted as `…+N`.
 */
export function summarizePaths(paths: readonly string[], max: number): string {
  const kept: string[] = [];
  let length = 0;
  for (const [index, path] of paths.entries()) {
    const remaining = paths.length - index - 1;
    const suffix = remaining > 0 ? `,…+${String(remaining)}` : '';
    const next = length + (kept.length > 0 ? 1 : 0) + path.length;
    if (next + suffix.length > max) {
      const dropped = paths.length - kept.length;
      return kept.length > 0
        ? `${kept.join(',')},…+${String(dropped)}`
        : `…+${String(dropped)}`;
    }
    kept.push(path);
    length = next;
  }
  return kept.join(',');
}
