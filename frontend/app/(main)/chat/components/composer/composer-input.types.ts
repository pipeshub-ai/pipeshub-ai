import type React from 'react';

export interface ComposerValue {
  text: string;
}

export interface ComposerInputHandle {
  focus(): void;
  clear(): void;
  setText(t: string): void;
  /** Types text at the caret as the user would. */
  insertText(text: string): void;
  getValue(): ComposerValue;
}

export interface ComposerInputProps {
  value: string;
  interim?: string;
  readOnly?: boolean;
  placeholder: string;
  maxHeightPx?: number;
  onChange(text: string): void;
  onKeyDown(e: React.KeyboardEvent): void;
  onFocus?(): void;
  onBlur?(): void;
  ariaLabel: string;
}
