'use client';

import React, { useState } from 'react';
import { Flex, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FieldLabel } from './field-label';
import type { PasswordFieldDef } from '../../constants';

interface PasswordInputFieldProps {
  field: PasswordFieldDef;
  value: string;
  onChange: (val: string) => void;
}

export function PasswordInputField({ field, value, onChange }: PasswordInputFieldProps) {
  const [showSecret, setShowSecret] = useState(false);

  return (
    <Flex direction="column" gap="1">
      <FieldLabel label={field.label} labelSuffix={field.labelSuffix} required={field.required} />
      <TextField.Root
        type={showSecret ? 'text' : 'password'}
        placeholder={field.placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <TextField.Slot side="right">
          <span
            onClick={() => setShowSecret((v) => !v)}
            style={{ cursor: 'pointer', display: 'flex', alignItems: 'center' }}
          >
            <MaterialIcon
              name={showSecret ? 'visibility_off' : 'visibility'}
              size={16}
              color="var(--slate-9)"
            />
          </span>
        </TextField.Slot>
      </TextField.Root>
      {field.helperText && (
        <Text size="1" style={{ color: 'var(--slate-10)' }}>
          {field.helperText}
        </Text>
      )}
    </Flex>
  );
}
