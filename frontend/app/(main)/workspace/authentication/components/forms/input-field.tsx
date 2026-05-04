'use client';

import React from 'react';
import { Flex, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FieldLabel } from './field-label';
import type { TextFieldDef } from '../../constants';

interface InputFieldProps {
  field: TextFieldDef;
  value: string;
  onChange: (val: string) => void;
}

export function InputField({ field, value, onChange }: InputFieldProps) {
  return (
    <Flex direction="column" gap="1">
      <FieldLabel label={field.label} labelSuffix={field.labelSuffix} required={field.required} />
      <TextField.Root
        placeholder={field.placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {field.icon && (
          <TextField.Slot>
            <MaterialIcon name={field.icon} size={14} color="var(--slate-9)" />
          </TextField.Slot>
        )}
      </TextField.Root>
      {field.helperText && (
        <Text size="1" style={{ color: 'var(--slate-10)' }}>
          {field.helperText}
        </Text>
      )}
    </Flex>
  );
}
