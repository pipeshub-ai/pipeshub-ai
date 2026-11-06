'use client';

import type { ReactNode } from 'react';
import { Flex, Text } from '@radix-ui/themes';

/** Past four the stack stops being scannable and becomes a texture. */
const STACK_CAP = 4;
const CHIP = 18;

export interface LogoStackMark {
  key: string;
  /** Read in the stack's tooltip. */
  label: string;
  /** The logo, drawn at 12px inside its disc. */
  logo: ReactNode;
}

/**
 * Overlapping logos in discs, capped with a "+N": the places an answer drew on, or the tools it could not use, said
 * without words. Decoration: the control it sits in names them.
 */
export function LogoStack({ marks, cap = STACK_CAP }: { marks: readonly LogoStackMark[]; cap?: number }) {
  const shown = marks.slice(0, cap);
  const extra = marks.length - shown.length;
  return (
    <Flex align="center" aria-hidden title={marks.map((m) => m.label).join(' · ')} style={{ flexShrink: 0 }}>
      {shown.map((mark, i) => (
        <Flex
          key={mark.key}
          align="center"
          justify="center"
          style={{
            width: CHIP,
            height: CHIP,
            borderRadius: '50%',
            background: 'var(--fill-muted)',
            // The ring is the ground's colour (the right column's card, else the chat), so each disc cuts a clean edge out of the one behind it.
            boxShadow: '0 0 0 1.5px var(--panel-surface, var(--surface-raised))',
            marginLeft: i === 0 ? 0 : -6,
            zIndex: i,
            flexShrink: 0,
            overflow: 'hidden',
          }}
        >
          {mark.logo}
        </Flex>
      ))}
      {extra > 0 && (
        <Flex
          align="center"
          justify="center"
          style={{
            height: CHIP,
            paddingInline: 5,
            marginLeft: -6,
            borderRadius: CHIP / 2,
            background: 'var(--fill-muted)',
            boxShadow: '0 0 0 1.5px var(--panel-surface, var(--surface-raised))',
            zIndex: shown.length,
          }}
        >
          <Text weight="medium" style={{ fontSize: 10, color: 'var(--text-secondary)' }}>
            +{extra}
          </Text>
        </Flex>
      )}
    </Flex>
  );
}
