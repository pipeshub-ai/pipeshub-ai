import { describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({ apiClient: {} }));
vi.mock('@/lib/i18n', () => ({
  i18n: { t: (_key: string, v: { kind: string; file: string }) => `${v.kind} in ${v.file}` },
}));

import { readableNode } from '../api';

describe('readableNode', () => {
  it('names an imports block by its file instead of its hash', () => {
    const n = { id: 'block:1', kind: 'imports', label: 'imports:@38ce6710', file: 'backend/db/versions/0003_rls.py' };
    expect(readableNode(n).label).toBe('imports in 0003_rls.py');
  });
  it('leaves a definition and a filler block without a file alone', () => {
    const fn = { id: 'block:2', kind: 'function', label: 'run_sql', file: 'a/b.py' };
    expect(readableNode(fn)).toBe(fn);
    const bare = { id: 'block:3', kind: 'imports', label: 'imports:@1' };
    expect(readableNode(bare)).toBe(bare);
  });
});
