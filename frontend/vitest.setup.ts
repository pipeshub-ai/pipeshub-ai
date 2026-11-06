import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// With `globals: false` Testing Library cannot register its own cleanup, so a test that forgets
// to unmount leaves components (and their timers) alive past jsdom teardown.
afterEach(() => {
  cleanup();
});

// jsdom sends a relative URL to its own origin (http://localhost:3000), where a real server may be listening. A
// request there that no test mocked fails as it would with nothing listening, without opening a socket.
if (typeof XMLHttpRequest !== 'undefined') {
  const { open, send } = XMLHttpRequest.prototype;
  // Taken before any test fakes the timers, which must not hold the failure back.
  const later = globalThis.setTimeout.bind(globalThis);
  const offline = new WeakSet<XMLHttpRequest>();
  XMLHttpRequest.prototype.open = function (this: XMLHttpRequest, method: string, url: string | URL, ...rest: unknown[]) {
    const base = window.location.href;
    if (URL.canParse(url, base) && new URL(url, base).origin === window.location.origin) offline.add(this);
    else offline.delete(this);
    return (open as (...args: unknown[]) => void).call(this, method, url, ...rest);
  } as typeof open;
  XMLHttpRequest.prototype.send = function (this: XMLHttpRequest, body?: Document | XMLHttpRequestBodyInit | null) {
    if (!offline.has(this)) return send.call(this, body);
    later(() => ['error', 'loadend'].forEach((type) => this.dispatchEvent(new ProgressEvent(type))));
  };
}
