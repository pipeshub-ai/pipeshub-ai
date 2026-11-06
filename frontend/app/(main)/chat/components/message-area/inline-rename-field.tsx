'use client';

import React, { useLayoutEffect, useRef, useState } from 'react';
import { TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

/**
 * Rename in place, the one field every chat bar uses for it: it opens with the whole name selected; Enter or leaving
 * the field commits, Escape keeps the old name, and an empty or unchanged name is a cancel. Committing on blur keeps
 * typing a stray click would otherwise throw away.
 *
 * The field keeps its own resting width (about twenty characters) instead of stretching across the bar: a longer name
 * scrolls inside it, and it only narrows when the bar has less room.
 */
export function InlineRenameField({
  value: initial,
  onCommit,
  onCancel,
}: {
  value: string;
  /** Called once with the trimmed new name. */
  onCommit: (next: string) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const ref = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState(initial);
  // Enter commits and then blurs: the blur must not commit a second time.
  const done = useRef(false);

  useLayoutEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);

  const commit = () => {
    if (done.current) return;
    done.current = true;
    const next = value.trim();
    if (next && next !== initial) onCommit(next);
    else onCancel();
  };

  return (
    <TextField.Root
      ref={ref}
      size="2"
      value={value}
      aria-label={t('chat.renameFieldLabel')}
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === 'Enter') {
          e.preventDefault();
          commit();
        } else if (e.key === 'Escape') {
          e.preventDefault();
          done.current = true;
          onCancel();
        }
      }}
      // The extra space-1 on the right keeps the field a full space-2 clear of the first control's box, whose ghost
      // button reaches that far past its cluster when the bar is narrow.
      style={{ flex: '0 1 auto', minWidth: 0, marginRight: 'var(--space-1)' }}
    />
  );
}
