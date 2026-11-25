'use client';

import React, { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { engineName, reasonKey } from '../access-copy';
import type { CodingEngineOption } from '../types';

const selectStyle: React.CSSProperties = {
  backgroundColor: 'var(--gray-2)',
  color: 'var(--gray-12)',
  border: '1px solid var(--gray-5)',
  borderRadius: 'var(--radius-2)',
  padding: '0 8px',
  height: '36px',
  fontSize: '14px',
  width: '100%',
  maxWidth: 420,
  outline: 'none',
  appearance: 'auto',
};

export interface CodingEngineChoice {
  value: string;
  label: string;
}

/** Why an engine cannot be used now, in the pickers' words (the server's code; a generic one if unknown). */
export function useEngineReason(): (reason: string | null) => string {
  const { t } = useTranslation();
  return (reason) => {
    const code = reason ?? 'unknown';
    return t(`aiAccounts.codingEngine.reasons.${code}`, { defaultValue: t(reasonKey(code)) });
  };
}

/**
 * One coding-engine choice (ENGPICK): the fixed choices around the engines
 * the org allows. A native select, so a disabled
 * engine is announced with its reason, which is part of the option's text.
 */
export function CodingEngineSelect({
  label,
  helper,
  value,
  leading,
  engines,
  trailing = [],
  onChange,
  disabled = false,
  error = null,
}: {
  label: string;
  helper: string;
  value: string;
  leading: readonly CodingEngineChoice[];
  engines: readonly CodingEngineOption[];
  trailing?: readonly CodingEngineChoice[];
  onChange: (value: string) => void;
  disabled?: boolean;
  error?: string | null;
}) {
  const id = useId();
  return (
    <Flex direction="column" gap="1">
      <Text as="label" htmlFor={id} size="2" weight="bold">
        {label}
      </Text>
      <select
        id={id}
        value={value}
        aria-describedby={`${id}-helper${error ? ` ${id}-error` : ''}`}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        style={selectStyle}
      >
        <CodingEngineOptions value={value} leading={leading} engines={engines} trailing={trailing} />
      </select>
      <Text id={`${id}-helper`} size="1" color="gray">
        {helper}
      </Text>
      {error && (
        <Text id={`${id}-error`} size="1" role="alert" style={{ color: 'var(--red-11)' }}>
          {error}
        </Text>
      )}
    </Flex>
  );
}

/** The options of a coding-engine select: an engine the org cannot use now is disabled with its reason in its text. */
export function CodingEngineOptions({
  value,
  leading,
  engines,
  trailing = [],
}: {
  value: string;
  leading: readonly CodingEngineChoice[];
  engines: readonly CodingEngineOption[];
  trailing?: readonly CodingEngineChoice[];
}) {
  const reasonOf = useEngineReason();
  const fixed = [...leading, ...trailing];
  const listed = engines.some((engine) => engine.kind === value) || fixed.some((c) => c.value === value);
  return (
    <>
      {leading.map((choice) => (
        <option key={choice.value} value={choice.value}>
          {choice.label}
        </option>
      ))}
      {engines.map((engine) => (
        <option key={engine.kind} value={engine.kind} disabled={!engine.available}>
          {engine.available ? engineName(engine.kind) : `${engineName(engine.kind)} – ${reasonOf(engine.reason)}`}
        </option>
      ))}
      {trailing.map((choice) => (
        <option key={choice.value} value={choice.value}>
          {choice.label}
        </option>
      ))}
      {!listed && (
        // A stored engine the org no longer allows stays visible, never silently swapped.
        <option value={value} disabled>
          {`${engineName(value)} – ${reasonOf('engine_profile_widening')}`}
        </option>
      )}
    </>
  );
}
