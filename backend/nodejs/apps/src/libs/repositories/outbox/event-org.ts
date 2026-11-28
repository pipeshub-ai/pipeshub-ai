import { idText } from '../import';

/**
 * The organisation an outbox event belongs to, on every store: the top-level `payload.orgId` of its envelope
 * (`{eventType, payload}`), else the organisation an `org:<id>` / `user:<id>:...` ordering key names. Lower-case hex, or null
 * when the event names none. An `orgId` nested deeper in the payload is data about something else and never counts.
 */
export function eventOrgOf(
  value: unknown,
  orderingKey: unknown,
): string | null {
  try {
    const org = (
      JSON.parse(String(value)) as { payload?: { orgId?: unknown } } | null
    )?.payload?.orgId;
    const id = idText(org);
    if (id) return id;
  } catch {
    // not JSON: fall through to the ordering key
  }
  const m = /^(?:user:|org:)([0-9a-fA-F]{24})(?::|$)/.exec(
    typeof orderingKey === 'string' ? orderingKey : '',
  );
  return m ? (m[1] as string).toLowerCase() : null;
}
