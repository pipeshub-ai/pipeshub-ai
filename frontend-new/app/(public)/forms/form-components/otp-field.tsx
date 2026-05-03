'use client';

import React, { useState } from 'react';
import { Flex, Text, TextField, IconButton } from '@radix-ui/themes';

export const OTP_LENGTH = 6;

export interface OtpFieldProps {
  value: string;
  onChange: (value: string) => void;
  label?: string;
  placeholder?: string;
  error?: string;
  autoFocus?: boolean;
  id?: string;
}

/**
 * Single-field OTP input (6 digits). Numeric-only; extra characters are stripped.
 */
const OtpField = React.forwardRef<HTMLInputElement, OtpFieldProps>(function OtpField(
  {
    value,
    onChange,
    label = 'OTP',
    placeholder = '******',
    error,
    autoFocus = false,
    id = 'otp-field',
  },
  ref,
) {
  const [focused, setFocused] = useState(false);
  const [visible, setVisible] = useState(false);

  const handleChange = (raw: string) => {
    const digits = raw.replace(/\D/g, '').slice(0, OTP_LENGTH);
    onChange(digits);
  };

  const showAccentRing = focused && !error;

  return (
    <Flex direction="column" gap="1">
      <Text
        as="label"
        htmlFor={id}
        style={{
          color: 'var(--gray-12)',
          fontSize: '14px',
          fontWeight: 500,
          lineHeight: '20px',
        }}
      >
        {label}
      </Text>
      <TextField.Root
        ref={ref}
        id={id}
        type={visible ? 'text' : 'password'}
        inputMode="numeric"
        autoComplete="one-time-code"
        value={value}
        onChange={(e) => handleChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        placeholder={placeholder}
        autoFocus={autoFocus}
        maxLength={OTP_LENGTH}
        size="3"
        color={error ? 'red' : 'jade'}
        style={{
          width: '100%',
          letterSpacing: '0.2em',
          outline: error
            ? '1px solid var(--red-8)'
            : showAccentRing
              ? '2px solid var(--accent-9)'
              : undefined,
        }}
      >
        <TextField.Slot side="right">
          <IconButton
            type="button"
            size="1"
            variant="ghost"
            color="gray"
            onClick={() => setVisible((v) => !v)}
            aria-label={visible ? 'Hide OTP' : 'Show OTP'}
          >
            <span
              className="material-icons-outlined"
              style={{ fontSize: '16px', color: 'var(--gray-10)' }}
            >
              {visible ? 'visibility_off' : 'visibility'}
            </span>
          </IconButton>
        </TextField.Slot>
      </TextField.Root>
      {error && (
        <Text size="1" style={{ color: 'var(--red-11)', fontWeight: 500 }}>
          {error}
        </Text>
      )}
    </Flex>
  );
});

export default OtpField;
