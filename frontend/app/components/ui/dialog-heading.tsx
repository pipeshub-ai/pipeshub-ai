'use client';

import React from 'react';
import { Dialog, Flex } from '@radix-ui/themes';

export interface DialogHeadingProps {
  /** The visible, translated question or task, which is also the dialog's accessible name. */
  title: React.ReactNode;
  /** The dialog's accessible description. */
  description?: React.ReactNode;
  /** A line under the description, in the same block. */
  note?: React.ReactNode;
}

/**
 * A centred dialog's title and description. A screen reader names the dialog by what is on screen, in the reader's
 * language, rather than by a hidden English title.
 */
export function DialogHeading({ title, description, note }: DialogHeadingProps) {
  return (
    <Flex direction="column" gap="1">
      <Dialog.Title size="5" weight="bold" mb="0" style={{ color: 'var(--gray-12)' }}>
        {title}
      </Dialog.Title>
      {description !== undefined ? (
        <Dialog.Description size="2" style={{ color: 'var(--slate-11)', overflowWrap: 'anywhere' }}>
          {description}
        </Dialog.Description>
      ) : null}
      {note}
    </Flex>
  );
}
