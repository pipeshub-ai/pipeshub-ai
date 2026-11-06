'use client';

import React from 'react';
import { useFormFieldControl } from '../../components/form-field';

interface GroupNameInputProps {
  value: string;
  canEditName: boolean;
  ariaLabel: string;
  onChange: (value: string) => void;
}

export function GroupNameInput({
  value,
  canEditName,
  ariaLabel,
  onChange,
}: GroupNameInputProps) {
  // Inside a FormField the control carries its error line and invalid state.
  const control = useFormFieldControl();
  const invalid = control['aria-invalid'] === true;
  const idleBorder = invalid ? '1px solid var(--red-a8)' : '1px solid var(--slate-a5)';
  return (
    <input
      type="text"
      {...control}
      aria-label={ariaLabel}
      value={value}
      onChange={(e) => {
        if (canEditName) onChange(e.target.value);
      }}
      readOnly={!canEditName}
      style={{
        width: '100%',
        height: 44, // the touch-target minimum; also ~12px around the 20px line
        padding: '0 12px',
        backgroundColor: 'var(--color-surface)',
        border: idleBorder,
        borderRadius: 'var(--radius-2)',
        fontSize: 14,
        lineHeight: '20px',
        fontFamily: 'var(--default-font-family)',
        color: 'var(--slate-12)',
        outline: 'none',
        boxSizing: 'border-box',
        cursor: canEditName ? 'text' : 'default',
      }}
      onFocus={(e) => {
        if (canEditName) {
          e.currentTarget.style.border = '2px solid var(--accent-8)';
          e.currentTarget.style.padding = '0 11px';
        }
      }}
      onBlur={(e) => {
        e.currentTarget.style.border = idleBorder;
        e.currentTarget.style.padding = '0 12px';
      }}
    />
  );
}
