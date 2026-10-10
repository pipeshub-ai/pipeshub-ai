'use client';

import React, { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import type { ComposerInputHandle, ComposerInputProps } from './composer-input.types';

const DEFAULT_MAX_HEIGHT_PX = 120;

export interface TextareaComposerInputProps extends ComposerInputProps {
  /** Dimmed text, used while the composer sits underneath an open overlay panel. Not part of the port. */
  muted?: boolean;
}

function syncHeight(el: HTMLTextAreaElement, maxHeightPx: number) {
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, maxHeightPx)}px`;
}

/** A textarea cannot carry structured mentions: `getValue().mentions` is always empty. */
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
        insertMention: (m, label) => {
          const el = textareaRef.current;
          const current = latest.current.value;
          const at = el?.selectionStart ?? current.length;
          const to = el?.selectionEnd ?? at;
          const insert = `@${label || m.id} `;
          latest.current.onChange(current.slice(0, at) + insert + current.slice(to));
        },
        insertText: (text) => {
          const el = textareaRef.current;
          const current = latest.current.value;
          const at = el?.selectionStart ?? current.length;
          const to = el?.selectionEnd ?? at;
          latest.current.onChange(current.slice(0, at) + text + current.slice(to));
          el?.focus();
        },
        getValue: () => ({ text: latest.current.value, mentions: [] }),
      }),
      [],
    );

    const displayValue = interim ? value + (value.length > 0 ? ' ' : '') + interim : value;

    return (
      <textarea
        ref={textareaRef}
        data-testid="chat-composer"
        aria-label={ariaLabel}
        value={displayValue}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onFocus={onFocus}
        onBlur={onBlur}
        placeholder={placeholder}
        readOnly={readOnly}
        rows={1}
        style={{
          width: '100%',
          backgroundColor: 'transparent',
          outline: 'none',
          border: 'none',
          fontSize: 'var(--font-size-2)',
          lineHeight: 1.5,
          resize: 'none',
          minHeight: '24px',
          maxHeight: `${maxHeightPx}px`,
          fontFamily: 'Manrope, sans-serif',
          height: 'auto',
          overflow: 'auto',
          padding: 0,
          margin: 0,
          color: muted ? 'var(--slate-11)' : readOnly ? 'var(--slate-a8)' : 'var(--slate-12)',
        }}
        onInput={(e) => syncHeight(e.currentTarget, maxHeightPx)}
      />
    );
  },
);
