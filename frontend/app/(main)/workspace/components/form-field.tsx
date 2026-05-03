'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';

// ========================================
// Types
// ========================================

interface FormFieldProps {
  /** Label text displayed above the field */
  label: string;
  /** Shows a mandatory field asterisk after the label */
  required?: boolean;
  /** Optional "(optional)" suffix */
  optional?: boolean;
  /** Error message displayed below the field */
  error?: string;
  /** Field content */
  children: React.ReactNode;
}

// ========================================
// Component
// ========================================

export function FormField({
  label,
  required,
  optional,
  error,
  children,
}: FormFieldProps) {
  return (
    <Flex direction="column" gap="1">
      <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
        {label}
        {required && !optional && (
          <Text
            as="span"
            weight="medium"
            style={{ color: 'var(--red-a11)', marginLeft: 2 }}
            aria-hidden
          >
            *
          </Text>
        )}
        {optional && (
          <Text
            size="1"
            weight="regular"
            style={{ color: 'var(--slate-9)', marginLeft: 'var(--space-1)' }}
            as="span"
          >
            (optional)
          </Text>
        )}
      </Text>
      {children}
      {error && (
        <Text size="1" style={{ color: 'var(--red-a11)', lineHeight: '16px' }}>
          {error}
        </Text>
      )}
    </Flex>
  );
}

export type { FormFieldProps };
