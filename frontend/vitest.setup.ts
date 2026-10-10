import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// With `globals: false` Testing Library cannot register its own cleanup, so a test that forgets
// to unmount leaves components (and their timers) alive past jsdom teardown.
afterEach(() => {
  cleanup();
});
