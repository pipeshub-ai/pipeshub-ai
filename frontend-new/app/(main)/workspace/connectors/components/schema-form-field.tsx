'use client';

import React, { useState } from 'react';
import { Flex, Text, Box, Checkbox, Switch, Select, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FormField } from '@/app/(main)/workspace/components/form-field';
import type { SchemaField } from '../types';

// ========================================
// Types
// ========================================

interface SchemaFormFieldProps {
  field: SchemaField;
  value: unknown;
  onChange: (name: string, value: unknown) => void;
  disabled?: boolean;
  /** For SELECT/MULTISELECT with dynamic options */
  options?: { label: string; value: string }[];
  /** For conditional display — if false, component returns null */
  visible?: boolean;
  /** Error message */
  error?: string;
}

// ========================================
// Shared input style
// ========================================

const inputStyle: React.CSSProperties = {
  height: 32,
  paddingTop: 6,
  paddingBottom: 6,
  paddingLeft: 8,
  paddingRight: 8,
  backgroundColor: 'var(--color-surface)',
  border: '1px solid var(--gray-a5)',
  borderRadius: 'var(--radius-2)',
  fontSize: 14,
  fontFamily: 'var(--default-font-family)',
  color: 'var(--gray-12)',
  boxSizing: 'border-box',
  width: '100%',
  outline: 'none',
};

const textareaStyle: React.CSSProperties = {
  ...inputStyle,
  height: 80,
  resize: 'vertical',
};

const focusStyle: React.CSSProperties = {
  border: '2px solid var(--accent-8)',
  paddingTop: 5,
  paddingBottom: 5,
  paddingLeft: 7,
  paddingRight: 7,
};

// ========================================
// Component
// ========================================

export function SchemaFormField({
  field,
  value,
  onChange,
  disabled = false,
  options,
  visible = true,
  error,
}: SchemaFormFieldProps) {
  if (!visible) return null;

  const fieldType = field.fieldType || 'TEXT';
  const _label = `${field.displayName}${'required' in field && field.required ? ' *' : ''}`;
  const isOptional = 'required' in field && !field.required;

  // Render the appropriate input based on field type
  const renderField = () => {
    switch (fieldType) {
      case 'CHECKBOX':
        return (
          <CheckboxField field={field} value={value} onChange={onChange} disabled={disabled} />
        );

      case 'BOOLEAN':
        return (
          <BooleanField field={field} value={value} onChange={onChange} disabled={disabled} />
        );

      default: {
        // All other field types use the FormField label wrapper
        const renderInput = () => {
          switch (fieldType) {
            case 'PASSWORD':
              return <PasswordInput field={field} value={value} onChange={onChange} disabled={disabled} />;
            case 'TEXTAREA':
              return <TextareaInput field={field} value={value} onChange={onChange} disabled={disabled} />;
            case 'JSON':
              return <JsonInput field={field} value={value} onChange={onChange} disabled={disabled} />;
            case 'SELECT':
              return <SelectInput field={field} value={value} onChange={onChange} disabled={disabled} options={options} />;
            case 'NUMBER':
              return <NumberInput field={field} value={value} onChange={onChange} disabled={disabled} />;
            default:
              // TEXT, EMAIL, URL, and fallback
              return <TextInput field={field} value={value} onChange={onChange} disabled={disabled} fieldType={fieldType} />;
          }
        };

        return (
          <FormField label={field.displayName} optional={isOptional} error={error}>
            {renderInput()}
          </FormField>
        );
      }
    }
  };

  return (
    <Flex direction="column" gap="1">
      {renderField()}

      {/* Description below the field (when not using FormField wrapper for checkbox/boolean) */}
      {field.description && (fieldType === 'CHECKBOX' || fieldType === 'BOOLEAN') && (
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          {field.description}
        </Text>
      )}
    </Flex>
  );
}

// ========================================
// Sub-components for each field type
// ========================================

function TextInput({
  field,
  value,
  onChange,
  disabled,
  fieldType,
}: {
  field: SchemaField;
  value: unknown;
  onChange: (name: string, value: unknown) => void;
  disabled: boolean;
  fieldType: string;
}) {
  const [isFocused, setIsFocused] = useState(false);

  const htmlType =
    fieldType === 'EMAIL' ? 'email' :
    fieldType === 'URL' ? 'url' :
    'text';

  return (
    <>
      <input
        type={htmlType}
        value={String(value ?? '')}
        placeholder={'placeholder' in field ? (field.placeholder ?? '') : ''}
        disabled={disabled}
        onChange={(e) => onChange(field.name, e.target.value)}
        onFocus={() => setIsFocused(true)}
        onBlur={() => setIsFocused(false)}
        style={{
          ...inputStyle,
          ...(isFocused ? focusStyle : {}),
          opacity: disabled ? 0.6 : 1,
        }}
      />
      {field.description && (
        <Text size="1" style={{ color: 'var(--gray-10)', marginTop: 2 }}>
          {field.description}
        </Text>
      )}
    </>
  );
}

function PasswordInput({
  field,
  value,
  onChange,
  disabled,
}: {
  field: SchemaField;
  value: unknown;
  onChange: (name: string, value: unknown) => void;
  disabled: boolean;
}) {
  const [showPassword, setShowPassword] = useState(false);
  const [isFocused, setIsFocused] = useState(false);

  return (
    <>
      <Box style={{ position: 'relative', width: '100%' }}>
        <input
          type={showPassword ? 'text' : 'password'}
          value={String(value ?? '')}
          placeholder={'placeholder' in field ? (field.placeholder ?? '') : ''}
          disabled={disabled}
          onChange={(e) => onChange(field.name, e.target.value)}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          style={{
            ...inputStyle,
            ...(isFocused ? focusStyle : {}),
            paddingRight: 36,
            opacity: disabled ? 0.6 : 1,
          }}
        />
        <IconButton
          type="button"
          variant="ghost"
          color="gray"
          size="1"
          onClick={() => setShowPassword(!showPassword)}
          style={{
            position: 'absolute',
            right: 6,
            top: 8,
            cursor: 'pointer',
          }}
        >
          <MaterialIcon
            name={showPassword ? 'visibility_off' : 'visibility'}
            size={16}
            color="var(--gray-11)"
          />
        </IconButton>
      </Box>
      {field.description && (
        <Text size="1" style={{ color: 'var(--gray-10)', marginTop: 2 }}>
          {field.description}
        </Text>
      )}
    </>
  );
}

function TextareaInput({
  field,
  value,
  onChange,
  disabled,
}: {
  field: SchemaField;
  value: unknown;
  onChange: (name: string, value: unknown) => void;
  disabled: boolean;
}) {
  const [isFocused, setIsFocused] = useState(false);

  return (
    <>
      <textarea
        value={String(value ?? '')}
        placeholder={'placeholder' in field ? (field.placeholder ?? '') : ''}
        disabled={disabled}
        onChange={(e) => onChange(field.name, e.target.value)}
        onFocus={() => setIsFocused(true)}
        onBlur={() => setIsFocused(false)}
        style={{
          ...textareaStyle,
          ...(isFocused ? { ...focusStyle, height: 80 } : {}),
          opacity: disabled ? 0.6 : 1,
        }}
      />
      {field.description && (
        <Text size="1" style={{ color: 'var(--gray-10)', marginTop: 2 }}>
          {field.description}
        </Text>
      )}
    </>
  );
}

function JsonInput({
  field,
  value,
  onChange,
  disabled,
}: {
  field: SchemaField;
  value: unknown;
  onChange: (name: string, value: unknown) => void;
  disabled: boolean;
}) {
  const [isFocused, setIsFocused] = useState(false);

  const stringVal =
    typeof value === 'string' ? value : JSON.stringify(value ?? '', null, 2);

  return (
    <>
      <textarea
        value={stringVal}
        placeholder={'placeholder' in field ? (field.placeholder ?? '{}') : '{}'}
        disabled={disabled}
        onChange={(e) => onChange(field.name, e.target.value)}
        onFocus={() => setIsFocused(true)}
        onBlur={() => setIsFocused(false)}
        style={{
          ...textareaStyle,
          ...(isFocused ? { ...focusStyle, height: 120 } : {}),
          height: 120,
          fontFamily: 'monospace',
          fontSize: 13,
          opacity: disabled ? 0.6 : 1,
        }}
      />
      {field.description && (
        <Text size="1" style={{ color: 'var(--gray-10)', marginTop: 2 }}>
          {field.description}
        </Text>
      )}
    </>
  );
}

function NumberInput({
  field,
  value,
  onChange,
  disabled,
}: {
  field: SchemaField;
  value: unknown;
  onChange: (name: string, value: unknown) => void;
  disabled: boolean;
}) {
  const [isFocused, setIsFocused] = useState(false);

  const min = 'validation' in field ? field.validation?.minLength : undefined;
  const max = 'validation' in field ? field.validation?.maxLength : undefined;

  return (
    <>
      <input
        type="number"
        value={value !== undefined && value !== null ? String(value) : ''}
        placeholder={'placeholder' in field ? (field.placeholder ?? '') : ''}
        disabled={disabled}
        min={min}
        max={max}
        onChange={(e) => {
          const num = e.target.value === '' ? '' : Number(e.target.value);
          onChange(field.name, num);
        }}
        onFocus={() => setIsFocused(true)}
        onBlur={() => setIsFocused(false)}
        style={{
          ...inputStyle,
          ...(isFocused ? focusStyle : {}),
          opacity: disabled ? 0.6 : 1,
        }}
      />
      {field.description && (
        <Text size="1" style={{ color: 'var(--gray-10)', marginTop: 2 }}>
          {field.description}
        </Text>
      )}
    </>
  );
}

function SelectInput({
  field,
  value,
  onChange,
  disabled,
  options,
}: {
  field: SchemaField;
  value: unknown;
  onChange: (name: string, value: unknown) => void;
  disabled: boolean;
  options?: { label: string; value: string }[];
}) {
  // Build options list from field.options or external options prop
  const optionItems = options ||
    ('options' in field && Array.isArray(field.options)
      ? field.options.map((opt: string | { id: string; label: string }) =>
          typeof opt === 'string'
            ? { label: opt, value: opt }
            : { label: opt.label, value: opt.id }
        )
      : []);

  return (
    <>
      <Select.Root
        value={String(value ?? '')}
        onValueChange={(v) => onChange(field.name, v)}
        disabled={disabled}
      >
        <Select.Trigger
          style={{ width: '100%', height: 32 }}
          placeholder={'placeholder' in field ? (field.placeholder ?? 'Select...') : 'Select...'}
        />
        <Select.Content>
          {optionItems.map((opt) => (
            <Select.Item key={opt.value} value={opt.value}>
              {opt.label}
            </Select.Item>
          ))}
        </Select.Content>
      </Select.Root>
      {field.description && (
        <Text size="1" style={{ color: 'var(--gray-10)', marginTop: 2 }}>
          {field.description}
        </Text>
      )}
    </>
  );
}

function CheckboxField({
  field,
  value,
  onChange,
  disabled,
}: {
  field: SchemaField;
  value: unknown;
  onChange: (name: string, value: unknown) => void;
  disabled: boolean;
}) {
  return (
    <Flex align="center" gap="2" style={{ minHeight: 32 }}>
      <Checkbox
        checked={Boolean(value)}
        onCheckedChange={(checked) => onChange(field.name, checked)}
        disabled={disabled}
      />
      <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
        {field.displayName}
        {'required' in field && field.required && ' *'}
      </Text>
    </Flex>
  );
}

function BooleanField({
  field,
  value,
  onChange,
  disabled,
}: {
  field: SchemaField;
  value: unknown;
  onChange: (name: string, value: unknown) => void;
  disabled: boolean;
}) {
  // Normalize string "true" / "false" to boolean
  const boolVal =
    typeof value === 'boolean'
      ? value
      : value === 'true'
      ? true
      : value === 'false'
      ? false
      : Boolean(value);

  return (
    <Flex align="center" justify="between" style={{ minHeight: 32 }}>
      <Flex direction="column" gap="1">
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {field.displayName}
        </Text>
      </Flex>
      <Switch
        checked={boolVal}
        onCheckedChange={(checked) => onChange(field.name, checked)}
        disabled={disabled}
      />
    </Flex>
  );
}

export type { SchemaFormFieldProps };
