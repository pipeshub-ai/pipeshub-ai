'use client';

import React, { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { MentionComposerField } from '@/chat/components/mention-composer-field';
import type { MentionNode } from '@/chat/utils/node-mentions';
import type { ComposerInputHandle, ComposerInputProps } from './composer-input.types';

const DEFAULT_MAX_HEIGHT_PX = 160;
const NO_MENTIONS: readonly MentionNode[] = [];
const ignoreMentions = () => {};

export interface TextareaComposerInputProps extends ComposerInputProps {
  /** Dimmed text, used while the composer sits underneath an open overlay panel. */
  muted?: boolean;
}

function syncHeight(el: HTMLTextAreaElement, maxHeightPx: number) {
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, maxHeightPx)}px`;
}

/** The plain composer: a textarea with `@` mentions of knowledge nodes, behind `ComposerInputHandle`. */
export const TextareaComposerInput = forwardRef<ComposerInputHandle, TextareaComposerInputProps>(
  function TextareaComposerInput(
    {
      value,
      interim,
      readOnly,
      placeholder,
      maxHeightPx = DEFAULT_MAX_HEIGHT_PX,
      onChange,
      onKeyDown,
      onFocus,
      onBlur,
      ariaLabel,
      muted,
      mentions = NO_MENTIONS,
      onMentionsChange,
      mentionParent,
      mentionsOff,
      mentionSelection,
      onMentionSelectionChange,
    },
    ref,
  ) {
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const latest = useRef({ value, onChange });
    useEffect(() => {
      latest.current = { value, onChange };
    });

    useImperativeHandle(
      ref,
      () => ({
        focus: () => textareaRef.current?.focus(),
        clear: () => {
          latest.current.onChange('');
          if (textareaRef.current) textareaRef.current.style.height = 'auto';
        },
        setText: (t) => latest.current.onChange(t),
        insertText: (text) => {
          const el = textareaRef.current;
          const current = latest.current.value;
          const at = el?.selectionStart ?? current.length;
          const to = el?.selectionEnd ?? at;
          latest.current.onChange(current.slice(0, at) + text + current.slice(to));
          el?.focus();
        },
        getValue: () => ({ text: latest.current.value }),
      }),
      [],
    );

    const displayValue = interim ? value + (value.length > 0 ? ' ' : '') + interim : value;

    return (
      <MentionComposerField
        ref={textareaRef}
        data-testid="chat-composer"
        aria-label={ariaLabel}
        value={displayValue}
        mentions={mentions}
        onChange={onChange}
        onMentionsChange={onMentionsChange ?? ignoreMentions}
        // With nowhere to keep a mention, `@` stays plain text.
        mentionsOff={mentionsOff || !onMentionsChange}
        parent={mentionParent}
        selection={mentionSelection}
        onSelectionChange={onMentionSelectionChange}
        onKeyDown={onKeyDown}
        onFocus={onFocus}
        onBlur={onBlur}
        placeholder={placeholder}
        readOnly={readOnly}
        multiline
        style={{
          width: '100%',
          backgroundColor: 'transparent',
          outline: 'none',
          border: 'none',
          fontSize: 'var(--font-size-2)',
          lineHeight: '20px',
          letterSpacing: 0,
          resize: 'none',
          maxHeight: `${maxHeightPx}px`,
          fontFamily: 'Manrope, sans-serif',
          height: 'auto',
          overflow: 'auto',
          padding: 0,
          margin: 0,
          color: muted ? 'var(--slate-11)' : readOnly ? 'var(--slate-a8)' : 'var(--neutral-12)',
        }}
        onInput={(e) => syncHeight(e.currentTarget, maxHeightPx)}
      />
    );
  },
);
