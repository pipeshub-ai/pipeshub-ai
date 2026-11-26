/**
 * The graph's changes the control plane's directory caches follow (02-lld
 * §8.5), published by Python on `entity-events` with the platform's envelope
 * `{eventType, payload, timestamp}`. Ids only: no names, no content.
 */
export type DirectoryEvent =
  | {
      readonly type: 'teamMembershipChanged';
      readonly orgId: string;
      readonly teamId: string;
      readonly userIds: readonly string[];
    }
  | {
      readonly type: 'agentChanged';
      readonly orgId: string;
      readonly agentKey: string;
    };

export const ENTITY_EVENTS_TOPIC = 'entity-events';

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

const id = (value: unknown): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= 256;

/** One message's directory event; every other event type, and anything malformed, is `null`, never a throw. */
export function directoryEventOf(value: unknown): DirectoryEvent | null {
  let message: unknown = value;
  if (typeof message === 'string') {
    try {
      message = JSON.parse(message) as unknown;
    } catch {
      return null;
    }
  }
  if (!isRecord(message) || !isRecord(message['payload'])) return null;
  const payload = message['payload'];
  const orgId = payload['orgId'];
  if (!id(orgId)) return null;
  if (message['eventType'] === 'teamMembershipChanged') {
    const teamId = payload['teamId'];
    const userIds = payload['userIds'];
    if (!id(teamId) || !Array.isArray(userIds) || !userIds.every(id))
      return null;
    return { type: 'teamMembershipChanged', orgId, teamId, userIds };
  }
  if (message['eventType'] === 'agentChanged') {
    const agentKey = payload['agentKey'];
    return id(agentKey) ? { type: 'agentChanged', orgId, agentKey } : null;
  }
  return null;
}
