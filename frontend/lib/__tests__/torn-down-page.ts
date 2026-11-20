import { vi } from 'vitest';
// jsdom's map from a DOM object to its implementation: what `window.close()` uses to drop listeners.
import jsdomUtils from 'jsdom/lib/jsdom/living/generated/utils.js';

type ListenerStore = { _eventListeners: object };

/**
 * Runs the pending fake timers on a torn-down page, the way the test runner leaves it at the end
 * of a file. It closes the jsdom window, which drops the window's and the document's listeners
 * and empties the page, and then deletes `window` and `document`. A timer that still reaches for
 * either global throws `ReferenceError`. It is rethrown here, including when it was thrown inside
 * an event listener, where jsdom would only report it. Everything comes back afterwards.
 */
export function runTimersWithPageTornDown() {
  const { jsdom } = globalThis as unknown as { jsdom: { window: Window } };
  const win = jsdom.window;
  const stores = [win, document].map((target) => jsdomUtils.implForWrapper(target) as ListenerStore);
  const listeners = stores.map((store) => store._eventListeners);
  for (const store of stores) store._eventListeners = Object.create(null);
  document.body.replaceChildren();

  const reported: unknown[] = [];
  const collect = (event: ErrorEvent) => {
    reported.push(event.error);
    event.preventDefault();
  };
  win.addEventListener('error', collect);

  const globals = globalThis as unknown as Record<string, unknown>;
  const saved = (['window', 'document'] as const).map((key) => [key, Object.getOwnPropertyDescriptor(globals, key)] as const);
  for (const [key] of saved) delete globals[key];
  try {
    vi.runOnlyPendingTimers();
  } finally {
    for (const [key, descriptor] of saved) if (descriptor) Object.defineProperty(globals, key, descriptor);
    win.removeEventListener('error', collect);
    stores.forEach((store, i) => {
      store._eventListeners = listeners[i];
    });
  }
  if (reported.length > 0) throw reported[0];
}
