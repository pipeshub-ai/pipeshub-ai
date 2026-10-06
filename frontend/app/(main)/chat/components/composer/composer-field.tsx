'use client';

import React, { forwardRef } from 'react';
import type { ComposerInputHandle } from './composer-input.types';
import { TextareaComposerInput } from './textarea-composer-input';
import { TiptapComposerInput, type TiptapComposerInputProps } from './tiptap-composer-input';

export interface ComposerFieldProps extends TiptapComposerInputProps {
  /** `ENABLE_CHAT_MENTIONS`: the rich editor with mentions. Off keeps the textarea exactly as before. */
  rich: boolean;
}

/** Picks the `ComposerInput` adapter. The textarea ignores the rich-only props. */
export const ComposerField = forwardRef<ComposerInputHandle, ComposerFieldProps>(function ComposerField(
  { rich, shouldDeferPaste, ...props },
  ref,
) {
  return rich ? (
    <TiptapComposerInput ref={ref} shouldDeferPaste={shouldDeferPaste} {...props} />
  ) : (
    <TextareaComposerInput ref={ref} {...props} />
  );
});
