'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Button, Flex, IconButton, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { DirectoryOption } from '../approvals/types';

/**
 * Search the org's agents, groups or users and pick one. It sits in wide pages and in the agent builder's 340 px card,
 * so the field and its button share a line only when both fit, and a long name or email wraps inside its button.
 */
export function DirectoryPicker({
  label,
  search,
  exclude,
  onPick,
}: {
  label: string;
  search: (query: string) => Promise<DirectoryOption[]>;
  exclude: string[];
  onPick: (option: DirectoryOption) => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<DirectoryOption[] | null>(null);
  const [failed, setFailed] = useState(false);
  const run = async () => {
    setFailed(false);
    try {
      setResults(await search(query.trim()));
    } catch {
      setResults(null);
      setFailed(true);
    }
  };
  const shown = (results ?? []).filter((option) => !exclude.includes(option.id));
  return (
    <Flex direction="column" gap="2">
      <Flex gap="2" align="center" wrap="wrap">
        <TextField.Root
          aria-label={label}
          placeholder={t('approvals.policy.searchPlaceholder')}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void run();
          }}
          style={{ flex: '1 1 16rem', minWidth: 0 }}
        />
        <Button type="button" variant="soft" onClick={() => void run()}>
          {t('approvals.policy.search')}
        </Button>
      </Flex>
      {failed && (
        <Text size="1" role="alert" style={{ color: 'var(--red-11)' }}>
          {t('approvals.policy.searchFailed')}
        </Text>
      )}
      {results !== null && shown.length === 0 && !failed && (
        <Text size="1" style={{ color: 'var(--slate-11)' }}>
          {t('approvals.policy.noResults')}
        </Text>
      )}
      {shown.length > 0 && (
        <Flex gap="2" wrap="wrap" role="list" aria-label={t('approvals.policy.results', { label })}>
          {shown.map((option) => (
            <Button
              key={option.id}
              type="button"
              size="1"
              variant="outline"
              role="listitem"
              aria-label={t('approvals.policy.addNamed', { name: option.name })}
              onClick={() => onPick(option)}
              style={{ maxWidth: '100%', height: 'auto', minHeight: 32, whiteSpace: 'normal', overflowWrap: 'anywhere', textAlign: 'start' }}
            >
              <MaterialIcon name="add" size={14} />
              {option.name}
              {option.detail ? ` · ${option.detail}` : ''}
            </Button>
          ))}
        </Flex>
      )}
    </Flex>
  );
}

export function DirectoryChips({
  ids,
  names,
  label,
  onRemove,
}: {
  ids: string[];
  names: Record<string, string>;
  label: string;
  onRemove: (id: string) => void;
}) {
  const { t } = useTranslation();
  if (ids.length === 0) {
    return (
      <Text size="2" style={{ color: 'var(--slate-11)' }}>
        {t('approvals.policy.none')}
      </Text>
    );
  }
  return (
    <Flex gap="2" wrap="wrap" role="list" aria-label={label}>
      {ids.map((id) => (
        <Badge key={id} size="2" variant="soft" role="listitem">
          {names[id] ?? id}
          <IconButton
            type="button"
            size="1"
            variant="ghost"
            aria-label={t('approvals.policy.removeNamed', { name: names[id] ?? id })}
            onClick={() => onRemove(id)}
          >
            <MaterialIcon name="close" size={12} />
          </IconButton>
        </Badge>
      ))}
    </Flex>
  );
}
