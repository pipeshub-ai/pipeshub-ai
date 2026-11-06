'use client';

import React, { forwardRef } from 'react';
import type { ComposerInputHandle } from './composer-input.types';
import { TextareaComposerInput, type TextareaComposerInputProps } from './textarea-composer-input';

export interface PlainComposerFieldProps extends TextareaComposerInputProps {
  /** Asks for the rich editor; the plain field is always a textarea. */
  rich?: boolean;
  shouldDeferPaste?(data: DataTransfer | null): boolean;
}

/** The plain composer field: a textarea behind `ComposerInputHandle`. */
export const ComposerField = forwardRef<ComposerInputHandle, PlainComposerFieldProps>(function ComposerField(
  { rich: _rich, shouldDeferPaste: _shouldDeferPaste, ...props },
  ref,
) {
  return <TextareaComposerInput ref={ref} {...props} />;
});
