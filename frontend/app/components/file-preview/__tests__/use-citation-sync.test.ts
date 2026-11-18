import { describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useCitationSync } from '../use-citation-sync';

const citations = [{ id: 'c1', content: 'a', pageNumbers: [4] }];

describe('useCitationSync', () => {
  it('the same citation twice navigates twice', () => {
    const onPageChange = vi.fn();
    const hook = renderHook(
      (jumpSeq: number) =>
        useCitationSync({ citations, currentPage: 1, onPageChange, initialPage: 4, initialCitationId: 'c1', jumpSeq }),
      { initialProps: 0 },
    );
    expect(onPageChange).toHaveBeenCalledTimes(1);
    const firstVersion = hook.result.current.citationClickVersion;

    hook.rerender(1);

    expect(onPageChange).toHaveBeenCalledTimes(2);
    expect(onPageChange).toHaveBeenLastCalledWith(4);
    expect(hook.result.current.citationClickVersion).toBeGreaterThan(firstVersion);
    expect(hook.result.current.activeCitationId).toBe('c1');
  });
});
