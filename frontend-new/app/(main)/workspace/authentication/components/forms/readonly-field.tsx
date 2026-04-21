'use client';

import React from 'react';
import { Flex, Text, TextField, Callout } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FieldLabel } from './field-label';
import type { ReadonlyFieldDef } from '../../constants';

interface ReadonlyFieldProps {
  field: ReadonlyFieldDef;
  value: string;
  warned: boolean;
}

export function ReadonlyField({ field, value, warned }: ReadonlyFieldProps) {
  return (
    <Flex direction="column" gap="1">
      <FieldLabel label={field.label} labelSuffix={field.labelSuffix} />
      <TextField.Root
        value={value}
        readOnly
        style={{ backgroundColor: 'var(--slate-3)', cursor: 'default' }}
      />
      {field.helperText && (
        <Text size="1" style={{ color: 'var(--slate-10)' }}>
          {field.helperText}
        </Text>
      )}
      {warned && field.warningText && (
        <Callout.Root color="amber" size="1" style={{ marginTop: 4 }}>
          <Callout.Icon>
            <MaterialIcon name="warning" size={14} color="var(--amber-11)" />
          </Callout.Icon>
          <Callout.Text>{field.warningText}</Callout.Text>
        </Callout.Root>
      )}
    </Flex>
  );
}
