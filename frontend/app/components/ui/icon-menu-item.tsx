'use client';

import type { ComponentProps, ReactNode } from 'react';
import { DropdownMenu } from '@radix-ui/themes';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { MaterialIcon } from './MaterialIcon';

interface IconMenuItemProps extends Omit<ComponentProps<typeof DropdownMenu.Item>, 'children'> {
  /** Material Symbols name of the leading glyph. */
  icon: string;
  children: ReactNode;
}

/**
 * A menu item led by its glyph: 16px, in the secondary text colour, 8px from the words (the item's own gap). A red or
 * disabled item draws it in the item's colour, so it always matches its words. The glyph is decorative: it is hidden
 * from screen readers and left out of type-ahead, so typing "R" still lands on Rename.
 */
export function IconMenuItem({ icon, children, textValue, ...props }: IconMenuItemProps) {
  const ownColour = props.color !== undefined || props.disabled === true;
  return (
    <DropdownMenu.Item textValue={textValue ?? (typeof children === 'string' ? children : undefined)} {...props}>
      <span aria-hidden style={{ display: 'inline-flex', flexShrink: 0 }}>
        <MaterialIcon name={icon} size={ICON_SIZES.SECONDARY} color={ownColour ? undefined : 'var(--text-secondary)'} />
      </span>
      {children}
    </DropdownMenu.Item>
  );
}
