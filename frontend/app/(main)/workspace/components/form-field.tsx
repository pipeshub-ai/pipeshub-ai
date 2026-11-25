'use client';

import React, { createContext, useContext, useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Select, Text, TextArea, TextField } from '@radix-ui/themes';

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
  /** Help text below the field, linked to the control */
  description?: string;
  /** Error message displayed below the field */
  error?: string;
  /** Field content; a function gets the control ids, for a control that is not one of the FormField* wrappers */
  children: React.ReactNode | ((control: FormFieldControlIds) => React.ReactNode);
}

/** Ids a control inside {@link FormField} sets so the label, help and error describe it. */
export interface FormFieldControlIds {
  id: string;
  'aria-describedby'?: string;
}

const FormFieldControlContext = createContext<FormFieldControlIds | null>(null);

/** Spread on the input (or Select trigger) inside a FormField; empty outside one. */
export function useFormFieldControl(): FormFieldControlIds | Record<string, never> {
  return useContext(FormFieldControlContext) ?? {};
}

/** A text field inside a FormField, labelled by it and described by its help and error. */
export function FormFieldTextField(props: React.ComponentProps<typeof TextField.Root>) {
  const control = useFormFieldControl();
  return <TextField.Root {...control} {...props} />;
}

/** A text area inside a FormField, labelled by it and described by its help and error. */
export function FormFieldTextArea(props: React.ComponentProps<typeof TextArea>) {
  const control = useFormFieldControl();
  return <TextArea {...control} {...props} />;
}

/** A Select trigger inside a FormField, labelled by it and described by its help and error. */
export function FormFieldSelectTrigger(props: React.ComponentProps<typeof Select.Trigger>) {
  const control = useFormFieldControl();
  return <Select.Trigger {...control} {...props} />;
}

/** True when a label (often a backend display name) already says "(optional)", in English or `suffix`. */
export function labelEndsWithOptional(label: string, suffix: string): boolean {
  const end = label.trim().toLowerCase();
  return end.endsWith('(optional)') || end.endsWith(suffix.trim().toLowerCase());
}

// ========================================
// Component
// ========================================

export function FormField({
  label,
  required,
  optional,
  description,
  error,
  children,
}: FormFieldProps) {
  const { t } = useTranslation();
  const id = useId();
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  const describedBy = [description ? helpId : null, error ? errorId : null].filter(Boolean).join(' ');
  const control: FormFieldControlIds = { id, 'aria-describedby': describedBy || undefined };

  return (
    <Flex direction="column" gap="1">
      <Text as="label" htmlFor={id} size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
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
        {optional && !labelEndsWithOptional(label, t('form.optional')) && (
          <Text
            size="1"
            weight="regular"
            style={{ color: 'var(--slate-11)', marginLeft: 'var(--space-1)' }}
            as="span"
          >
            {t('form.optional')}
          </Text>
        )}
      </Text>
      <FormFieldControlContext.Provider value={control}>
        {typeof children === 'function' ? children(control) : children}
      </FormFieldControlContext.Provider>
      {description && (
        <Text id={helpId} size="1" style={{ color: 'var(--gray-11)', marginTop: 2 }}>
          {description}
        </Text>
      )}
      {error && (
        <Text id={errorId} role="alert" size="1" style={{ color: 'var(--red-a11)', lineHeight: '16px' }}>
          {error}
        </Text>
      )}
    </Flex>
  );
}

export type { FormFieldProps };
