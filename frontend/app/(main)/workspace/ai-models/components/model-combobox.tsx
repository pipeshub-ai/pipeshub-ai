'use client';

import React, { useContext, useEffect, useId, useMemo, useRef, useState } from 'react';
import { Badge, Flex, Popover, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';
import { FormField } from '@/app/(main)/workspace/components/form-field';
import {
  WORKSPACE_DRAWER_POPPER_Z_INDEX,
  WorkspaceRightPanelBodyPortalContext,
} from '@/app/(main)/workspace/components/workspace-right-panel';
import { aiModelFormOptions } from '@/config';
import type { DiscoveredModel } from '../types';

const MAX_VISIBLE_OPTIONS = 200;

const compactNumber = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });

type Option =
  | { kind: 'custom'; id: string }
  | { kind: 'model'; id: string; model: DiscoveredModel; group: 'main' | 'other' };

/** Main models first, deprecated ones last, unclassified ones in their own group. */
export function buildModelOptions(
  models: DiscoveredModel[],
  query: string,
  selected: ReadonlySet<string>,
): Option[] {
  const needle = query.trim().toLowerCase();
  // Every word must appear, in any order: a server search (the Hugging Face Hub)
  // returns multilingual-e5-small for "multilingual e5", and a local list should agree.
  const words = needle.split(/\s+/).filter(Boolean);
  const seen = new Set<string>();
  const main: Option[] = [];
  const retired: Option[] = [];
  const other: Option[] = [];
  for (const model of models) {
    const key = model.id.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    const id = model.id.toLowerCase();
    const name = model.displayName.toLowerCase();
    if (!words.every((word) => id.includes(word) || name.includes(word))) continue;
    const otherOnly = model.capabilities.length === 1 && model.capabilities[0] === 'other';
    const option: Option = { kind: 'model', id: model.id, model, group: otherOnly ? 'other' : 'main' };
    if (otherOnly) other.push(option);
    else if (model.deprecated) retired.push(option);
    else main.push(option);
  }
  const ordered = [...main, ...retired, ...other];
  // The id that was typed goes first, so the highlight and Enter pick it over a
  // newer id that merely contains it (gpt-4o-mini for gpt-4o).
  const exactIndex = needle ? ordered.findIndex((option) => option.id.toLowerCase() === needle) : -1;
  if (exactIndex > 0) ordered.unshift(...ordered.splice(exactIndex, 1));
  const listed = ordered.slice(0, MAX_VISIBLE_OPTIONS);
  const typed = query.trim();
  const exact = typed && (seen.has(typed.toLowerCase()) || selected.has(typed));
  if (!typed || exact) return listed;
  // Ids have no spaces, so text with spaces is a search: offer it as an id last,
  // after the matches, so Enter picks a real model.
  const custom: Option = { kind: 'custom', id: typed };
  return /\s/.test(typed) ? [...listed, custom] : [custom, ...listed];
}

interface ModelComboboxProps {
  label: string;
  required?: boolean;
  placeholder?: string;
  models: DiscoveredModel[];
  value: string[];
  onChange: (ids: string[]) => void;
  multiple: boolean;
  loading?: boolean;
  /** Shown under the field; typing a model id still works. */
  hint?: string | null;
  hintTone?: 'gray' | 'red';
  disabled?: boolean;
  /** For providers that search server-side instead of listing everything. */
  onQueryChange?: (query: string) => void;
  /**
   * Leaving the field keeps typed text as a model id only when no list is coming.
   * While a list is still loading, the text is a search, not an id.
   */
  commitTypedOnBlur?: boolean;
}

export function ModelCombobox({
  label,
  required,
  placeholder,
  models,
  value,
  onChange,
  multiple,
  loading = false,
  hint,
  hintTone = 'gray',
  disabled = false,
  onQueryChange,
  commitTypedOnBlur,
}: ModelComboboxProps) {
  const { t } = useTranslation();
  const listId = useId();
  const portal = useContext(WorkspaceRightPanelBodyPortalContext);
  const inputRef = useRef<HTMLInputElement>(null);
  const anchorRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const selected = useMemo(() => new Set(value), [value]);
  const options = useMemo(() => buildModelOptions(models, query, selected), [models, query, selected]);
  const byId = useMemo(() => new Map(models.map((model) => [model.id, model])), [models]);

  useEffect(() => {
    setActive(0);
  }, [query, models]);

  const updateQuery = (next: string) => {
    setQuery(next);
    onQueryChange?.(next);
  };

  const choose = (id: string) => {
    updateQuery('');
    if (!multiple) {
      onChange([id.split(',')[0].trim()]);
      setOpen(false);
      return;
    }
    if (selected.has(id)) {
      onChange(value.filter((item) => item !== id));
    } else {
      // A pasted "a, b" list becomes one chip per model.
      const added = id.split(',').map((part) => part.trim()).filter((part) => part && !selected.has(part));
      onChange([...value, ...new Set(added)]);
    }
    inputRef.current?.focus();
  };

  const remove = (id: string) => onChange(value.filter((item) => item !== id));

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setOpen(true);
      setActive((index) => Math.min(index + 1, Math.max(options.length - 1, 0)));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((index) => Math.max(index - 1, 0));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const option = open ? options[active] : undefined;
      if (option) choose(option.id);
      else if (query.trim()) choose(query.trim());
    } else if (event.key === 'Backspace' && !query && value.length > 0) {
      remove(value[value.length - 1]);
    }
  };

  const showChips = value.length > 0;
  const firstOtherIndex = options.findIndex((option) => option.kind === 'model' && option.group === 'other');

  return (
    <FormField label={label} required={required}>
      <Popover.Root open={open && !disabled} onOpenChange={setOpen}>
        {/* Themes' Popover.Anchor drops its children, so the field is the trigger; its toggle is cancelled below. */}
        <Popover.Trigger>
          <div
            ref={anchorRef}
            data-testid="ai-model-combobox"
            onClick={(event) => event.preventDefault()}
            onMouseDown={(event) => {
              if (event.target !== inputRef.current) event.preventDefault();
              inputRef.current?.focus();
              setOpen(true);
            }}
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              alignItems: 'center',
              gap: 4,
              minHeight: 32,
              padding: '3px 8px',
              backgroundColor: 'var(--color-surface)',
              border: `1px solid ${open ? 'var(--accent-8)' : 'var(--gray-a7)'}`,
              borderRadius: 'var(--radius-2)',
              cursor: disabled ? 'not-allowed' : 'text',
              opacity: disabled ? 0.6 : 1,
            }}
          >
            <MaterialIcon name="widgets" size={16} color="var(--gray-10)" />
            {value.map((id) => (
              <Badge key={id} variant="soft" color="gray" size="2" style={{ maxWidth: '100%' }}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {byId.get(id)?.displayName && byId.get(id)?.displayName !== id
                    ? `${byId.get(id)?.displayName} · ${id}`
                    : id}
                </span>
                <button
                  type="button"
                  aria-label={t('workspace.aiModels.modelPickerRemoveFor', { model: id })}
                  disabled={disabled}
                  onMouseDown={(event) => {
                    // Keep focus in the input so a half-typed id is not committed by its blur.
                    event.stopPropagation();
                    event.preventDefault();
                  }}
                  onClick={() => remove(id)}
                  style={{
                    display: 'inline-flex',
                    border: 'none',
                    background: 'none',
                    padding: 0,
                    cursor: 'pointer',
                    color: 'inherit',
                  }}
                >
                  <MaterialIcon name="close" size={14} />
                </button>
              </Badge>
            ))}
            <input
              ref={inputRef}
              role="combobox"
              aria-label={label}
              aria-expanded={open}
              aria-controls={listId}
              aria-autocomplete="list"
              value={query}
              disabled={disabled}
              placeholder={showChips ? (multiple ? t('workspace.aiModels.modelComboboxAddMore') : '') : placeholder}
              onChange={(event) => {
                updateQuery(event.target.value);
                setOpen(true);
              }}
              onFocus={() => setOpen(true)}
              onBlur={() => {
                if ((commitTypedOnBlur ?? models.length === 0) && query.trim()) choose(query.trim());
              }}
              onKeyDown={onKeyDown}
              style={{
                flex: '1 0 120px',
                minWidth: 120,
                border: 'none',
                outline: 'none',
                background: 'transparent',
                color: 'var(--gray-12)',
                fontSize: 14,
                height: 24,
              }}
            />
            {loading ? <Spinner size={14} /> : <MaterialIcon name="expand_more" size={18} color="var(--gray-10)" />}
          </div>
        </Popover.Trigger>
        <Popover.Content
          container={portal ?? undefined}
          align="start"
          sideOffset={4}
          onOpenAutoFocus={(event) => event.preventDefault()}
          // Radix closes the list from a document listener; stop the key there so the drawer's
          // window-level Escape handler does not close the whole panel too.
          onEscapeKeyDown={(event) => event.stopPropagation()}
          onInteractOutside={(event) => {
            if (anchorRef.current?.contains(event.target as Node)) event.preventDefault();
          }}
          style={{
            zIndex: WORKSPACE_DRAWER_POPPER_Z_INDEX,
            width: 'var(--radix-popover-trigger-width)',
            maxHeight: 320,
            overflowY: 'auto',
            padding: 4,
          }}
        >
          <div
            id={listId}
            role="listbox"
            aria-multiselectable={multiple}
            data-testid="ai-model-options"
            onMouseDown={(event) => event.preventDefault()}
          >
            {loading && models.length === 0 ? (
              <Text as="div" size="2" style={{ padding: '8px 10px', color: 'var(--gray-11)' }}>
                {t('workspace.aiModels.fetchingModels')}
              </Text>
            ) : null}
            {!loading && options.length === 0 ? (
              <Text as="div" size="2" style={{ padding: '8px 10px', color: 'var(--gray-11)' }}>
                {models.length === 0
                  ? t('workspace.aiModels.modelComboboxTypeHint')
                  : t('workspace.aiModels.modelPickerEmpty')}
              </Text>
            ) : null}
            {options.map((option, index) => (
              <React.Fragment key={`${option.kind}:${option.id}`}>
                {index === firstOtherIndex ? (
                  <Text
                    as="div"
                    size="1"
                    weight="medium"
                    style={{ padding: '8px 10px 4px', color: 'var(--gray-10)' }}
                  >
                    {t('workspace.aiModels.modelComboboxOtherGroup')}
                  </Text>
                ) : null}
                <div
                  role="option"
                  aria-selected={selected.has(option.id)}
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => choose(option.id)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '6px 10px',
                    borderRadius: 'var(--radius-2)',
                    cursor: 'pointer',
                    backgroundColor: index === active ? 'var(--accent-a3)' : 'transparent',
                  }}
                >
                  <span style={{ width: 16, display: 'inline-flex', flexShrink: 0 }}>
                    {selected.has(option.id) ? (
                      <MaterialIcon name="check" size={16} color="var(--accent-11)" />
                    ) : null}
                  </span>
                  {option.kind === 'custom' ? (
                    <Text size="2">{t('workspace.aiModels.modelComboboxUseTyped', { model: option.id })}</Text>
                  ) : (
                    <>
                      <Flex direction="column" style={{ minWidth: 0, flex: 1 }}>
                        <Text size="2" truncate>
                          {option.model.displayName || option.id}
                        </Text>
                        {option.model.displayName && option.model.displayName !== option.id ? (
                          <Text size="1" truncate style={{ color: 'var(--gray-10)' }}>
                            {option.id}
                          </Text>
                        ) : null}
                      </Flex>
                      <ModelMeta model={option.model} />
                    </>
                  )}
                </div>
              </React.Fragment>
            ))}
          </div>
        </Popover.Content>
      </Popover.Root>
      {hint ? (
        <Text size="1" style={{ color: hintTone === 'red' ? 'var(--red-11)' : 'var(--gray-10)' }}>
          {hint}
        </Text>
      ) : null}
    </FormField>
  );
}

/** Quiet one-line summary; a list of coloured chips per row is too loud to scan. */
function ModelMeta({ model }: { model: DiscoveredModel }) {
  const { t } = useTranslation();
  const parts = [
    model.contextLength
      ? t('workspace.aiModels.badgeContext', { value: compactNumber.format(model.contextLength) })
      : null,
    model.isMultimodal ? t('workspace.aiModels.badgeVision') : null,
    model.isReasoning ? t('workspace.aiModels.badgeReasoning') : null,
    ...aiModelFormOptions.discoveredModelBadges(t, model),
    model.deprecated ? t('workspace.aiModels.badgeDeprecated') : null,
  ].filter(Boolean);
  if (parts.length === 0) return null;
  return (
    <Text size="1" style={{ color: 'var(--gray-10)', flexShrink: 0, whiteSpace: 'nowrap' }}>
      {parts.join(' · ')}
    </Text>
  );
}
