'use client';

import React, { useEffect, useState } from 'react';
import { Flex, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FieldLabel } from './field-label';
import type { PasswordFieldDef } from '../../constants';
import { SecretTextField } from '@/app/components/ui/secret-text-field';

interface PasswordInputFieldProps {
  field: PasswordFieldDef;
  value: string;
  onChange: (val: string) => void;
  /** Set once the form has fetched the stored secret, so the field shows it instead of dots. */
  revealed?: boolean;
}

export function PasswordInputField({ field, value, onChange, revealed = false }: PasswordInputFieldProps) {
  const [showSecret, setShowSecret] = useState(false);
  useEffect(() => {
    if (revealed) setShowSecret(true);
  }, [revealed]);

  return (
    <Flex direction="column" gap="1">
      <FieldLabel label={field.label} labelSuffix={field.labelSuffix} required={field.required} />
      <SecretTextField
        masked={!showSecret}
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
              color="var(--slate-11)"
            />
          </span>
        </TextField.Slot>
      </SecretTextField>
      {field.helperText && (
        <Text size="1" style={{ color: 'var(--slate-11)' }}>
          {field.helperText}
        </Text>
      )}
    </Flex>
  );
}
