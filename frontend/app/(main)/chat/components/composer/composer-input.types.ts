import type React from 'react';

export type MentionRef = { type: 'assistant' | 'agent' | 'user' | 'team'; id: string };

export interface ComposerValue {
  text: string;
  mentions: MentionRef[];
}

export interface ComposerInputHandle {
  focus(): void;
  clear(): void;
  setText(t: string): void;
  insertMention(m: MentionRef, label: string): void;
  /** Types text at the caret as the user would, so an "@" opens the mention popover. */
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
