'use client';

import React, { useId, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, DropdownMenu, Flex, Popover, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { documentLayout } from './document-layout';
import { Field, SchemaForm } from './schema-form';
import type { FieldSpec } from './schema-fields';

interface DocumentFormProps {
  fields: FieldSpec[];
  values: Record<string, unknown>;
  /** A message per field name, already translated. */
  errors: Record<string, string>;
  /** Field names whose value differs from the call as asked. */
  changed?: Set<string>;
  onChange: (name: string, value: unknown) => void;
}

const blank = (v: unknown) => v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0);

/** The call's arguments laid out as the thing it makes: recipients, a title, settings as pills,
 * the text, and anything else folded under "More fields". */
export function DocumentForm({ fields, values, errors, changed, onChange }: DocumentFormProps) {
  const { t } = useTranslation();
  const base = useId();
  const layout = useMemo(() => documentLayout(fields), [fields]);
  const [moreOpen, setMoreOpen] = useState(false);
  const id = (field: FieldSpec) => `${base}-${field.name}`;
  const props = (field: FieldSpec) => ({
    id: id(field),
    field,
    value: values[field.name],
    error: errors[field.name],
    changed: changed?.has(field.name) ?? false,
    onChange: (value: unknown) => onChange(field.name, value),
  });
  const pillErrors = layout.pills.filter((f) => errors[f.name]);
  // A field with an error is never hidden, so the card can focus it.
  const showMore = moreOpen || layout.more.some((f) => errors[f.name]);

  return (
    <Flex direction="column" gap="3">
      {layout.recipients.map((field) => <Field key={field.name} {...props(field)} />)}
      {layout.headline && <Headline {...props(layout.headline)} />}
      {layout.pills.length > 0 && (
        <Flex direction="column" gap="1">
          <Flex wrap="wrap" gap="2">
            {layout.pills.map((field) => <Pill key={field.name} {...props(field)} />)}
          </Flex>
          {pillErrors.map((field) => (
            <Text key={field.name} id={`${id(field)}-pill-error`} size="1" style={{ color: 'var(--red-11)' }}>
              {t('chat.toolApproval.pill', { label: field.label, value: errors[field.name] })}
            </Text>
          ))}
        </Flex>
      )}
      {layout.body.map((field) => <Field key={field.name} {...props(field)} />)}
      {layout.more.length > 0 && (
        <Box>
          <Button size="1" variant="ghost" color="gray" aria-expanded={showMore} onClick={() => setMoreOpen((open) => !open)}>
            {t('chat.toolApproval.moreFields')}
            <MaterialIcon name={showMore ? 'expand_less' : 'expand_more'} size={16} />
          </Button>
          {showMore && (
            <Box mt="3">
              <SchemaForm fields={layout.more} values={values} errors={errors} changed={changed} onChange={onChange} />
            </Box>
          )}
        </Box>
      )}
    </Flex>
  );
}

type RegionProps = React.ComponentProps<typeof Field>;

function Headline({ id, field, value, error, changed, onChange }: RegionProps) {
  const { t } = useTranslation();
  const errorId = `${id}-error`;
  return (
    <Flex direction="column" gap="1">
      <Flex align="center" gap="2">
        <input
          id={id}
          type="text"
          aria-label={field.label}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          aria-required={field.required || undefined}
          placeholder={field.label}
          value={value === undefined || value === null ? '' : String(value)}
          onChange={(e) => onChange(e.target.value)}
          style={{
            flex: 1,
            minWidth: 0,
            border: 'none',
            backgroundColor: 'transparent',
            // Room above and below so descenders are never clipped.
            padding: 'var(--space-1) 0',
            fontFamily: 'var(--default-font-family)',
            fontSize: 'var(--font-size-3)',
            fontWeight: 'var(--font-weight-medium)',
            lineHeight: 1.3,
            color: 'var(--gray-12)',
          }}
        />
        {changed && <Badge size="1" variant="soft" color="gray">{t('chat.toolApproval.edited')}</Badge>}
      </Flex>
      {error && <Text id={errorId} size="1" style={{ color: 'var(--red-11)' }}>{error}</Text>}
    </Flex>
  );
}

function pillValue(field: FieldSpec, value: unknown, t: (key: string) => string): string {
  if (field.control === 'switch') return t(value === true ? 'common.yes' : 'common.no');
  if (field.control === 'select') return field.options?.find((o) => o.value === value)?.label ?? String(value);
  return Array.isArray(value) ? value.join(', ') : String(value);
}

function Pill({ id, field, value, error, changed, onChange }: RegionProps) {
  const { t } = useTranslation();
  const empty = blank(value) && field.control !== 'switch';
  const text = empty
    ? t('chat.toolApproval.pillEmpty', { label: field.label })
    : t('chat.toolApproval.pill', { label: field.label, value: pillValue(field, value, t) });
  const look: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 'var(--space-1)',
    maxWidth: '100%',
    height: 'calc(var(--space-6) - var(--space-1))',
    padding: '0 var(--space-2)',
    border: `1px solid ${error ? 'var(--red-8)' : 'var(--gray-6)'}`,
    borderRadius: 'var(--radius-full)',
    backgroundColor: 'var(--gray-2)',
    color: empty ? 'var(--gray-11)' : 'var(--gray-12)',
    fontFamily: 'var(--default-font-family)',
    fontSize: 'var(--font-size-1)',
    cursor: 'pointer',
  };
  const a11y = {
    type: 'button' as const,
    'aria-invalid': error ? true : undefined,
    'aria-describedby': error ? `${id}-pill-error` : undefined,
    style: look,
  };
  const face = (icon: boolean) => (
    <>
      <Text truncate>{text}</Text>
      {changed && <Badge size="1" variant="soft" color="gray">{t('chat.toolApproval.edited')}</Badge>}
      {icon && <MaterialIcon name="expand_more" size={16} color="var(--gray-10)" />}
    </>
  );

  if (field.control === 'switch') {
    return (
      <button {...a11y} aria-pressed={value === true} onClick={() => onChange(value !== true)}>
        {face(false)}
      </button>
    );
  }

  if (field.control === 'select') {
    const options = field.options ?? [];
    const index = options.findIndex((o) => o.value === value);
    return (
      <DropdownMenu.Root>
        <DropdownMenu.Trigger>
          <button {...a11y}>{face(true)}</button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Content size="1">
          {/* Items are keyed by position so a numeric option is written back as the number it was. */}
          <DropdownMenu.RadioGroup value={index >= 0 ? String(index) : ''} onValueChange={(i) => onChange(options[Number(i)]?.value)}>
            {options.map((option, i) => (
              <DropdownMenu.RadioItem key={i} value={String(i)}>{option.label}</DropdownMenu.RadioItem>
            ))}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Root>
    );
  }

  return (
    <Popover.Root>
      <Popover.Trigger>
        <button {...a11y}>{face(true)}</button>
      </Popover.Trigger>
      <Popover.Content size="1" minWidth="240px" maxWidth="320px">
        <Field id={id} field={field} value={value} error={error} changed={changed} onChange={onChange} />
      </Popover.Content>
    </Popover.Root>
  );
}
