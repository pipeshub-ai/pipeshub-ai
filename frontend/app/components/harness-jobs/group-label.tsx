'use client';

import type { ReactNode } from 'react';
import { Text } from '@radix-ui/themes';

/**
 * The name of a group of things in a panel or a list (design `group-label.tsx`): "NEEDS YOU 1", "RUNNING", "WHAT IT
 * WAS GIVEN". Uppercase and small, so it is scanned rather than read and never competes with the first row under it.
 * `count` follows the name in the same muted voice. `as="h3"` when the label heads a region a screen reader jumps by.
 */
export function GroupLabel({ children, count, as = 'span', id }: { children: ReactNode; count?: number; as?: 'span' | 'div' | 'h3'; id?: string }) {
  const style = { color: 'var(--gray-11)', textTransform: 'uppercase' as const, letterSpacing: '0.4px', margin: 0, fontWeight: 500 };
  const body = (
    <>
      {children}
      {/* A space, not a margin: the label's name reads "Needs you 1", not "Needs you1". */}
      {count !== undefined ? (
        <>
          {' '}
          <span style={{ color: 'var(--gray-11)' }}>{count}</span>
        </>
      ) : null}
    </>
  );
  if (as === 'h3') {
    return (
      <h3 id={id} data-testid="group-label" style={{ ...style, fontSize: 'var(--font-size-1)', lineHeight: 'var(--line-height-1)' }}>
        {body}
      </h3>
    );
  }
  return (
    <Text as={as} size="1" id={id} data-testid="group-label" style={style}>
      {body}
    </Text>
  );
}
