'use client';

import React from 'react';
import { Flex, Text, TextArea } from '@radix-ui/themes';
import { FieldLabel } from './field-label';
import type { TextareaFieldDef } from '../../constants';

interface TextareaFieldProps {
  field: TextareaFieldDef;
  value: string;
  onChange: (val: string) => void;
}

export function TextareaField({ field, value, onChange }: TextareaFieldProps) {
  return (
    <Flex direction="column" gap="1">
      <FieldLabel label={field.label} labelSuffix={field.labelSuffix} required={field.required} />
      <TextArea
        placeholder={field.placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          minHeight: field.minHeight ?? 80,
          ...(field.monospace ? { fontFamily: 'monospace', fontSize: 12 } : {}),
        }}
      />
      {field.helperText && (
        <Text size="1" style={{ color: 'var(--slate-10)' }}>
          {field.helperText}
        </Text>
      )}
    </Flex>
  );
}
