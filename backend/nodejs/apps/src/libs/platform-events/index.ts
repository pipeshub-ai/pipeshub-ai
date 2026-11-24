/**
 * Platform events: how application code says what happened.
 *
 * Application code announces a product fact with `emitPlatformEvent` and moves
 * on. It never imports whoever is interested: plug-ins (see `libs/plugins`)
 * subscribe with `onPlatformEvent`. A build without plug-ins has no listeners,
 * and an emit is then one map lookup.
 *
 * Rules the bus keeps:
 * - Emit is synchronous and never throws to the caller. A listener that throws
 *   (or returns a promise that rejects) is counted, reported to the error
 *   reporter when one is set, and does not stop the listeners after it.
 * - Listeners for a name run in the order they subscribed, then the `'*'`
 *   listeners in the order they subscribed.
 * - The payload is passed by reference, without cloning or freezing, so an
 *   emit costs nothing per listener. LISTENERS MUST NOT MUTATE THE PAYLOAD:
 *   the emitter and every later listener see the same object.
 *
 * This file imports nothing at run time, so the database layer can emit from
 * it while the rest of the application is still loading.
 */
import type {
  PlatformEventName,
  PlatformEventPayload,
  PlatformEvents,
} from './events';

export type { PlatformEventName, PlatformEventPayload, PlatformEvents };

/** A listener for one named event. */
export type PlatformEventHandler<K extends PlatformEventName> = (
  payload: PlatformEventPayload<K>,
  name: K,
) => unknown;

/** A listener for every event (`'*'`); the payload type depends on `name`. */
export type AnyPlatformEventHandler = (
  payload: PlatformEventPayload<PlatformEventName>,
  name: PlatformEventName,
) => unknown;

/** Told about each listener failure; must not throw (it is guarded anyway). */
export type PlatformEventErrorReporter = (name: string, error: unknown) => void;

type Listener = (payload: unknown, name: string) => unknown;

/** Events whose payload has no required field may be emitted without one. */
type EmitArgs<K extends PlatformEventName> =
  Record<string, never> extends PlatformEvents[K]
    ? [payload?: PlatformEvents[K]]
    : [payload: PlatformEvents[K]];

const WILDCARD = '*';

// Arrays are replaced on every change, never edited in place, so an emit that
// is walking one is not disturbed by a listener that subscribes or leaves.
const listeners = new Map<string, readonly Listener[]>();
let handlerErrors = 0;
let errorReporter: PlatformEventErrorReporter | undefined;

function failed(name: string, error: unknown): void {
  handlerErrors += 1;
  if (errorReporter === undefined) return;
  try {
    errorReporter(name, error);
  } catch {
    // A broken reporter must not reach the emitter either.
  }
}

function run(
  handlers: readonly Listener[],
  name: string,
  payload: unknown,
): void {
  for (const handler of handlers) {
    try {
      const result = handler(payload, name);
      if (result instanceof Promise) {
        result.catch((error: unknown) => {
          failed(name, error);
        });
      }
    } catch (error) {
      failed(name, error);
    }
  }
}

/**
 * Announce that something happened. Returns once every listener has been
 * called; with no listener it returns after two map lookups.
 */
export function emitPlatformEvent<K extends PlatformEventName>(
  name: K,
  ...args: EmitArgs<K>
): void {
  const named = listeners.get(name);
  const any = listeners.get(WILDCARD);
  if (named === undefined && any === undefined) return;
  const payload: unknown = args[0] ?? {};
  if (named !== undefined) run(named, name, payload);
  if (any !== undefined) run(any, name, payload);
}

/**
 * Listen for one event, or for all of them with `'*'`. Returns the function
 * that stops listening; calling it twice is harmless. The handler must not
 * mutate the payload (see the file comment).
 */
export function onPlatformEvent<K extends PlatformEventName>(
  name: K,
  handler: PlatformEventHandler<K>,
): () => void;
export function onPlatformEvent(
  name: '*',
  handler: AnyPlatformEventHandler,
): () => void;
export function onPlatformEvent(
  name: string,
  handler: (payload: never, name: never) => unknown,
): () => void {
  const listener = handler as Listener;
  listeners.set(name, [...(listeners.get(name) ?? []), listener]);
  let active = true;
  return () => {
    if (!active) return;
    active = false;
    const current = listeners.get(name) ?? [];
    const index = current.indexOf(listener);
    if (index === -1) return;
    const next = [...current.slice(0, index), ...current.slice(index + 1)];
    if (next.length === 0) listeners.delete(name);
    else listeners.set(name, next);
  };
}

/** How many listener calls have failed since start (or the last reset). */
export function platformEventStats(): { handlerErrors: number } {
  return { handlerErrors };
}

/** Where listener failures are reported besides the counter; one at a time. */
export function setPlatformEventErrorReporter(
  reporter: PlatformEventErrorReporter | undefined,
): void {
  errorReporter = reporter;
}

/** Drop every listener, the reporter and the failure count. For tests. */
export function resetPlatformEvents(): void {
  listeners.clear();
  handlerErrors = 0;
  errorReporter = undefined;
}
