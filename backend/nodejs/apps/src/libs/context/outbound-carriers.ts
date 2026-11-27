/**
 * Outbound context carriers.
 *
 * A call that leaves this process (an HTTP request to another service, a
 * message put on the broker) already carries the request id. A plug-in that
 * keeps more context for the request registers a carrier here, and its fields
 * travel with the same calls: `injectRequestHeaders` and `injectEnvelope`
 * hand every carrier the headers or the envelope they are about to return.
 *
 * Nothing is registered by default, and both functions then behave exactly as
 * they did without this file. This file imports nothing.
 */

/** What is being sent: HTTP request headers, or a broker message envelope. */
export type OutboundCarrierKind = 'http' | 'message';

export interface OutboundContextCarrier {
  /**
   * Lower-case names of the HTTP headers this carrier may add. Code that
   * forwards only an allow-list of headers lets these through as well.
   */
  readonly httpHeaders?: readonly string[];
  /**
   * Add fields to `carrier`, in place. `carrier` is already a copy, so the
   * caller's object is never changed. For `http` every value must be a string;
   * anything else is dropped. A carrier that throws is skipped.
   */
  inject(carrier: Record<string, unknown>, kind: OutboundCarrierKind): void;
}

// Replaced on every change, never edited in place: an inject that is walking
// the list is not disturbed by a carrier that registers or leaves.
let carriers: readonly OutboundContextCarrier[] = [];

/** Register a carrier. Returns the function that removes it again. */
export function registerOutboundContextCarrier(
  carrier: OutboundContextCarrier,
): () => void {
  carriers = [...carriers, carrier];
  return () => {
    carriers = carriers.filter((registered) => registered !== carrier);
  };
}

export function hasOutboundContextCarriers(): boolean {
  return carriers.length > 0;
}

/** Let every registered carrier add its fields to `carrier`. Never throws. */
export function applyOutboundContextCarriers(
  carrier: Record<string, unknown>,
  kind: OutboundCarrierKind,
): void {
  if (carriers.length === 0) return;
  const before = { ...carrier };
  for (const registered of carriers) {
    try {
      registered.inject(carrier, kind);
    } catch {
      // The call being made matters more than the extra context on it.
    }
  }
  if (kind !== 'http') return;
  // A header value is a string. What the caller passed in is left as it was.
  for (const [name, value] of Object.entries(carrier)) {
    if (typeof value === 'string' || value === before[name]) continue;
    if (name in before) carrier[name] = before[name];
    else delete carrier[name];
  }
}

/** Every header name the registered carriers may add, in lower case. */
export function outboundCarrierHeaderNames(): string[] {
  return carriers.flatMap((registered) =>
    (registered.httpHeaders ?? []).map((name) => name.toLowerCase()),
  );
}

/** Remove every carrier. For tests. */
export function resetOutboundContextCarriers(): void {
  carriers = [];
}
