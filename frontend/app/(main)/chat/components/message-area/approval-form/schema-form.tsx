'use client';

import React, { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Flex, Select, Switch, Text, TextArea, TextField } from '@radix-ui/themes';
import { TagInput, type TagItem } from '@/app/(main)/workspace/components';
import type { FieldSpec } from './schema-fields';

interface SchemaFormProps {
  fields: FieldSpec[];
  values: Record<string, unknown>;
  /** A message per field name, already translated. */
  errors: Record<string, string>;
  /** Field names whose value differs from the call as asked. */
  changed?: Set<string>;
  onChange: (name: string, value: unknown) => void;
}

const asText = (value: unknown) => (value === undefined || value === null ? '' : String(value));

// A number field keeps text that isn't a number as typed, so the check can say so.
function parseNumber(text: string): unknown {
  if (text.trim() === '') return undefined;
  const n = Number(text);
  return Number.isFinite(n) ? n : text;
}

const toTags = (value: unknown): TagItem[] =>
  (Array.isArray(value) ? value : []).map((item, i) => ({ id: `${i}:${String(item)}`, value: String(item) }));

/** The call's arguments as a form, one field per top-level property. */
export function SchemaForm({ fields, values, errors, changed, onChange }: SchemaFormProps) {
  const base = useId();
  return (
    <Flex direction="column" gap="3">
      {fields.map((field) => (
        <Field
          key={field.name}
          id={`${base}-${field.name}`}
          field={field}
          value={values[field.name]}
          error={errors[field.name]}
          changed={changed?.has(field.name) ?? false}
          onChange={(value) => onChange(field.name, value)}
        />
      ))}
    </Flex>
  );
}

interface FieldProps {
  id: string;
  field: FieldSpec;
  value: unknown;
  error?: string;
  changed: boolean;
  onChange: (value: unknown) => void;
}

function Field({ id, field, value, error, changed, onChange }: FieldProps) {
  const { t } = useTranslation();
  const errorId = `${id}-error`;
  const labelId = `${id}-label`;
  const a11y = {
    id,
    'aria-invalid': error ? true : undefined,
    'aria-describedby': error ? errorId : undefined,
  } as const;

  let control: React.ReactNode;
  switch (field.control) {
    case 'textarea':
      control = <TextArea {...a11y} size="2" rows={4} value={asText(value)} onChange={(e) => onChange(e.target.value)} style={{ resize: 'vertical' }} />;
      break;
    case 'number':
      control = <TextField.Root {...a11y} size="2" type="number" value={asText(value)} onChange={(e) => onChange(parseNumber(e.target.value))} />;
      break;
    case 'switch':
      control = <Switch {...a11y} aria-labelledby={labelId} checked={value === true} onCheckedChange={onChange} />;
      break;
    case 'select': {
      const options = field.options ?? [];
      const index = options.findIndex((o) => o.value === value);
      control = (
        // Items are keyed by position so a numeric option is written back as the number it was.
        <Select.Root value={index >= 0 ? String(index) : undefined} onValueChange={(i) => onChange(options[Number(i)]?.value)}>
          <Select.Trigger {...a11y} aria-labelledby={labelId} />
          <Select.Content>
            {options.map((option, i) => (
              <Select.Item key={i} value={String(i)}>{option.label}</Select.Item>
            ))}
          </Select.Content>
        </Select.Root>
      );
      break;
    }
    case 'chips':
      control = (
        <Box role="group" aria-labelledby={labelId} aria-describedby={a11y['aria-describedby']}>
          <TagInput compact freeText tags={toTags(value)} onTagsChange={(tags) => onChange(tags.map((tag) => tag.value))} />
        </Box>
      );
      break;
    case 'json':
      control = (
        <Box
          id={id}
          aria-labelledby={labelId}
          className="no-scrollbar"
          style={{
            border: '1px solid var(--slate-a5)',
            borderRadius: 'var(--radius-2)',
            backgroundColor: 'var(--slate-a2)',
            padding: 'var(--space-2)',
            maxHeight: 160,
            overflowY: 'auto',
          }}
        >
          <Text as="p" size="1" style={{ fontFamily: 'var(--code-font-family)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
            {JSON.stringify(value ?? null, null, 2)}
          </Text>
        </Box>
      );
      break;
    default: {
      // shortcut: a date-time stays a text field, because a datetime-local input drops the time zone; add a picker that keeps it if people need one.
      const type = field.control === 'email' ? 'email' : field.control === 'date' ? 'date' : 'text';
      control = <TextField.Root {...a11y} size="2" type={type} value={asText(value)} onChange={(e) => onChange(e.target.value)} />;
    }
  }

  return (
    <Flex direction="column" gap="1">
      <Flex align="center" gap="2">
        <Text as="label" id={labelId} htmlFor={field.control === 'json' ? undefined : id} size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
          {field.label}
          {field.required && <Text aria-hidden style={{ color: 'var(--red-11)' }}> *</Text>}
        </Text>
        {changed && <Badge size="1" variant="soft" color="amber">{t('chat.toolApproval.changed')}</Badge>}
      </Flex>
      {control}
      {field.control === 'json' && (
        <Text size="1" style={{ color: 'var(--slate-10)' }}>{t('chat.toolApproval.readOnlyField')}</Text>
      )}
      {error && (
        <Text id={errorId} size="1" style={{ color: 'var(--red-11)' }}>{error}</Text>
      )}
    </Flex>
  );
}
