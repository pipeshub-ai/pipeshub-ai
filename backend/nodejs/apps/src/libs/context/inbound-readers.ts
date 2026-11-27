/**
 * Inbound context readers: the receiving half of `./outbound-carriers`.
 *
 * Work that enters this process (an HTTP request, a message taken off the
 * broker) arrives with the headers or the envelope its sender stamped. A
 * plug-in that put fields there on the way out registers a reader here to get
 * them back on the way in: the HTTP context middleware and the broker
 * consumers hand every reader what arrived, and run the handler inside the
 * scope each reader returns, so what the plug-in restores lasts exactly as
 * long as the handler (an `AsyncLocalStorage.run`, typically).
 *
 * Nothing is registered by default, and the handler then runs exactly as it
 * did without this file. This file imports nothing.
 */

/** What arrived: HTTP request headers, or a broker message envelope. */
export type InboundCarrierKind = 'http' | 'message';

/** Runs `fn` with whatever the reader restored, and returns what `fn` returns. */
export type InboundScope = <T>(fn: () => T) => T;

export interface InboundContextReader {
  /**
   * Look at `carrier` (never change it) and return the scope the handler is
   * to run in, or nothing when there is none to restore. For `http` the keys
   * are lower-case header names. A reader that throws is skipped.
   */
  read(
    carrier: Readonly<Record<string, unknown>>,
    kind: InboundCarrierKind,
  ): InboundScope | void;
}

// Replaced on every change, never edited in place: a delivery that is walking
// the list is not disturbed by a reader that registers or leaves.
let readers: readonly InboundContextReader[] = [];

/** Register a reader. Returns the function that removes it again. */
export function registerInboundContextReader(
  reader: InboundContextReader,
): () => void {
  readers = [...readers, reader];
  return () => {
    readers = readers.filter((registered) => registered !== reader);
  };
}

export function hasInboundContextReaders(): boolean {
  return readers.length > 0;
}

function enter<T>(scopes: readonly InboundScope[], at: number, fn: () => T): T {
  const scope = scopes[at];
  if (scope === undefined) return fn();
  let entered = false;
  const rest = (): T => {
    entered = true;
    return enter(scopes, at + 1, fn);
  };
  try {
    return scope(rest);
  } catch (error) {
    // What the handler threw is the handler's. A scope that failed before it
    // got that far is skipped: the work matters more than the context on it.
    if (entered) throw error;
    return rest();
  }
}

/**
 * Run `fn` inside the scope of every reader that has one for `carrier`. With
 * no reader, or a carrier that is not an object, this is `fn()`. The readers
 * never make it throw; what `fn` throws (or rejects with) passes through.
 */
export function runWithInboundContext<T>(
  carrier: unknown,
  kind: InboundCarrierKind,
  fn: () => T,
): T {
  if (
    readers.length === 0 ||
    carrier === null ||
    typeof carrier !== 'object' ||
    Array.isArray(carrier)
  ) {
    return fn();
  }
  const scopes: InboundScope[] = [];
  for (const reader of readers) {
    try {
      const scope = reader.read(carrier as Record<string, unknown>, kind);
      if (typeof scope === 'function') scopes.push(scope);
    } catch {
      // Skipped, as above.
    }
  }
  return enter(scopes, 0, fn);
}

/** Remove every reader. For tests. */
export function resetInboundContextReaders(): void {
  readers = [];
}
