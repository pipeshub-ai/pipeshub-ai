'use client';

import React, { useState, useContext } from 'react';
import { Flex, Text, Box, Checkbox, Switch, Select, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FormField } from '@/app/(main)/workspace/components/form-field';
import { WorkspaceRightPanelBodyPortalContext } from '@/app/(main)/workspace/components/workspace-right-panel';
import { useToastStore } from '@/lib/store/toast-store';
import type { SchemaField } from '../types';

/** Extra left padding when `startAdornment` is set (icon column) */
const ADORNMENT_LEFT_GUTTER = 30;

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
  /**
   * When this field renders inside a portaled overlay (e.g. workspace drawer), set so
   * `Select.Content` stacks above the backdrop. Omit in normal inline forms.
   */
  selectPortalZIndex?: number;
  /** Optional icon or node inside the left side of text-like inputs */
  startAdornment?: React.ReactNode;
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
  selectPortalZIndex,
  startAdornment,
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
              return (
                <PasswordInput
                  field={field}
                  value={value}
                  onChange={onChange}
                  disabled={disabled}
                  startAdornment={startAdornment}
                />
              );
            case 'TEXTAREA':
              return <TextareaInput field={field} value={value} onChange={onChange} disabled={disabled} />;
            case 'JSON':
              return <JsonInput field={field} value={value} onChange={onChange} disabled={disabled} />;
            case 'SELECT':
              return (
                <SelectInput
                  field={field}
                  value={value}
                  onChange={onChange}
                  disabled={disabled}
                  options={options}
                  portalZIndex={selectPortalZIndex}
                  startAdornment={startAdornment}
                />
              );
            case 'NUMBER':
              return (
                <NumberInput field={field} value={value} onChange={onChange} disabled={disabled} startAdornment={startAdornment} />
              );
            default:
              // TEXT, EMAIL, URL, and fallback
              return (
                <TextInput
                  field={field}
                  value={value}
                  onChange={onChange}
                  disabled={disabled}
                  fieldType={fieldType}
                  startAdornment={startAdornment}
                />
              );
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

/**
 * Compact, copyable list of labeled example values rendered below an input.
 *
 * Used when a single `placeholder` can't convey all the variants a user may
 * need (e.g. Azure AI endpoint URLs differ per model family). Each row has a
 * label, the example value in a monospace pill, and a copy button so the user
 * doesn't have to hand-type or squint at a truncated placeholder.
 */
function FieldExamples({
  examples,
}: {
  examples: { label: string; value: string }[];
}) {
  const addToast = useToastStore((s) => s.addToast);
  const [copiedValue, setCopiedValue] = useState<string | null>(null);

  const handleCopy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopiedValue(value);
      window.setTimeout(() => {
        setCopiedValue((v) => (v === value ? null : v));
      }, 1800);
    } catch {
      addToast({
        variant: 'error',
        title: 'Failed to copy',
        duration: 3000,
      });
    }
  };

  return (
    <Box
      style={{
        marginTop: 6,
        padding: '8px 10px',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--gray-a4)',
        backgroundColor: 'var(--gray-a2)',
      }}
    >
      <Flex align="center" gap="1" style={{ marginBottom: 6 }}>
        <MaterialIcon name="info" size={14} color="var(--gray-10)" />
        <Text size="1" weight="medium" style={{ color: 'var(--gray-11)' }}>
          {examples.length > 1 ? 'Examples' : 'Example'}
        </Text>
      </Flex>
      <Flex direction="column" gap="2">
        {examples.map((ex) => {
          const justCopied = copiedValue === ex.value;
          return (
            <Flex key={`${ex.label}:${ex.value}`} direction="column" gap="1">
              <Text size="1" style={{ color: 'var(--gray-11)' }}>
                {ex.label}
              </Text>
              <Flex align="center" gap="2" style={{ minWidth: 0 }}>
                <Box
                  style={{
                    flex: 1,
                    minWidth: 0,
                    padding: '4px 8px',
                    borderRadius: 'var(--radius-1)',
                    border: '1px solid var(--gray-a5)',
                    backgroundColor: 'var(--color-surface)',
                    fontFamily: 'var(--code-font-family, ui-monospace, SFMono-Regular, Menlo, monospace)',
                    fontSize: 12,
                    color: 'var(--gray-12)',
                    overflowX: 'auto',
                    whiteSpace: 'nowrap',
                    userSelect: 'all',
                  }}
                >
                  {ex.value}
                </Box>
                <Tooltip content={justCopied ? 'Copied' : 'Copy to clipboard'}>
                  <IconButton
                    type="button"
                    variant="soft"
                    color="gray"
                    size="1"
                    aria-label={`Copy example ${ex.label}`}
                    onClick={() => {
                      void handleCopy(ex.value);
                    }}
                    style={{ cursor: 'pointer', flexShrink: 0 }}
                  >
                    <MaterialIcon
                      name={justCopied ? 'check' : 'content_copy'}
                      size={14}
                      color={justCopied ? 'var(--green-11)' : 'var(--gray-11)'}
                    />
                  </IconButton>
                </Tooltip>
              </Flex>
            </Flex>
          );
        })}
      </Flex>
    </Box>
  );
}

function StartAdornmentOverlay({
  startAdornment,
  children,
}: {
  startAdornment?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Box style={{ position: 'relative', width: '100%' }}>
      {startAdornment ? (
        <Flex
          align="center"
          justify="center"
          style={{
            position: 'absolute',
            left: 8,
            top: 0,
            bottom: 0,
            width: 22,
            pointerEvents: 'none',
            zIndex: 1,
          }}
        >
          {startAdornment}
        </Flex>
      ) : null}
      {children}
    </Box>
  );
}

function TextInput({
  field,
  value,
  onChange,
  disabled,
  fieldType,
  startAdornment,
}: {
  field: SchemaField;
  value: unknown;
  onChange: (name: string, value: unknown) => void;
  disabled: boolean;
  fieldType: string;
  startAdornment?: React.ReactNode;
}) {
  const [isFocused, setIsFocused] = useState(false);

  const htmlType =
    fieldType === 'EMAIL' ? 'email' :
    fieldType === 'URL' ? 'url' :
    'text';

  const leftGutter = startAdornment ? ADORNMENT_LEFT_GUTTER : 0;

  const examples =
    'examples' in field && Array.isArray(field.examples) ? field.examples : undefined;

  return (
    <>
      <StartAdornmentOverlay startAdornment={startAdornment}>
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
            paddingLeft: (isFocused ? 7 : 8) + leftGutter,
            opacity: disabled ? 0.6 : 1,
          }}
        />
      </StartAdornmentOverlay>
      {field.description && (
        <Text size="1" style={{ color: 'var(--gray-10)', marginTop: 2 }}>
          {field.description}
        </Text>
      )}
      {examples && examples.length > 0 ? <FieldExamples examples={examples} /> : null}
    </>
  );
}

function PasswordInput({
  field,
  value,
  onChange,
  disabled,
  startAdornment,
}: {
  field: SchemaField;
  value: unknown;
  onChange: (name: string, value: unknown) => void;
  disabled: boolean;
  startAdornment?: React.ReactNode;
}) {
  const [showPassword, setShowPassword] = useState(false);
  const [isFocused, setIsFocused] = useState(false);

  const leftGutter = startAdornment ? ADORNMENT_LEFT_GUTTER : 0;

  return (
    <>
      <StartAdornmentOverlay startAdornment={startAdornment}>
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
            paddingLeft: (isFocused ? 7 : 8) + leftGutter,
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
      </StartAdornmentOverlay>
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
  startAdornment,
}: {
  field: SchemaField;
  value: unknown;
  onChange: (name: string, value: unknown) => void;
  disabled: boolean;
  startAdornment?: React.ReactNode;
}) {
  const [isFocused, setIsFocused] = useState(false);

  const min = 'validation' in field ? field.validation?.minLength : undefined;
  const max = 'validation' in field ? field.validation?.maxLength : undefined;

  const leftGutter = startAdornment ? ADORNMENT_LEFT_GUTTER : 0;

  return (
    <>
      <StartAdornmentOverlay startAdornment={startAdornment}>
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
            paddingLeft: (isFocused ? 7 : 8) + leftGutter,
            opacity: disabled ? 0.6 : 1,
          }}
        />
      </StartAdornmentOverlay>
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
  portalZIndex,
  startAdornment,
}: {
  field: SchemaField;
  value: unknown;
  onChange: (name: string, value: unknown) => void;
  disabled: boolean;
  options?: { label: string; value: string }[];
  portalZIndex?: number;
  startAdornment?: React.ReactNode;
}) {
  const panelBodyPortal = useContext(WorkspaceRightPanelBodyPortalContext);

  // Build options list from field.options or external options prop
  const optionItems = (options ||
    ('options' in field && Array.isArray(field.options)
      ? field.options.map((opt: string | { id: string; label: string }) =>
          typeof opt === 'string'
            ? { label: opt, value: opt }
            : { label: opt.label, value: opt.id }
        )
      : [])).filter((opt) => opt.value !== '');

  const leftGutter = startAdornment ? ADORNMENT_LEFT_GUTTER : 0;

  return (
    <>
      <StartAdornmentOverlay startAdornment={startAdornment}>
        <Select.Root
          value={String(value ?? '')}
          onValueChange={(v) => onChange(field.name, v)}
          disabled={disabled}
        >
          <Select.Trigger
            style={{
              width: '100%',
              height: 32,
              paddingLeft: 8 + leftGutter,
            }}
            placeholder={'placeholder' in field ? (field.placeholder ?? 'Select...') : 'Select...'}
          />
          <Select.Content
            position="popper"
            container={panelBodyPortal ?? undefined}
            style={portalZIndex != null ? { zIndex: portalZIndex } : undefined}
          >
            {optionItems.map((opt) => (
              <Select.Item key={opt.value} value={opt.value}>
                {opt.label}
              </Select.Item>
            ))}
          </Select.Content>
        </Select.Root>
      </StartAdornmentOverlay>
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
