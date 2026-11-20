import { describe, expect, it } from 'vitest';
import { isChatRailPath } from '../chat-rail-routes';

describe('isChatRailPath: the pages that share one chat rail', () => {
  it.each(['/chat', '/chat/', '/agents', '/agents/', '/artifacts', '/artifacts/'])('%s shows the chat rail', (path) => {
    expect(isChatRailPath(path)).toBe(true);
  });

  it.each(['/agents/edit/', '/agents/new/', '/knowledge-base/', '/projects/', '/workspace/general/', '/', '/chatty/'])(
    '%s has a rail of its own, or none',
    (path) => {
      expect(isChatRailPath(path)).toBe(false);
    },
  );

  it('no path yet is no rail', () => {
    expect(isChatRailPath(null)).toBe(false);
  });
});
