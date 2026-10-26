import { describe, it, expect } from 'vitest';
import { decideDesktopGate } from '../desktop-mode';

describe('decideDesktopGate', () => {
  it('local mode always goes to the local start screen, whatever server URL was saved', () => {
    expect(decideDesktopGate({ mode: 'local' }, true)).toBe('local');
    expect(decideDesktopGate({ mode: 'local' }, false)).toBe('local');
  });

  it('first run with no server configured asks which mode to use', () => {
    expect(decideDesktopGate({ mode: null }, false)).toBe('choose');
  });

  it('an existing install (server URL acknowledged, no mode yet) keeps working unchanged', () => {
    expect(decideDesktopGate({ mode: null }, true)).toBe('ready');
    expect(decideDesktopGate({ mode: 'remote' }, true)).toBe('ready');
  });

  it('remote mode without a saved server asks for the server URL', () => {
    expect(decideDesktopGate({ mode: 'remote' }, false)).toBe('setup');
  });

  it('a shell without the desktop bridge asks for the server URL, as before', () => {
    expect(decideDesktopGate(null, false)).toBe('setup');
    expect(decideDesktopGate(null, true)).toBe('ready');
  });
});
