'use client';

import React, { useId, useState } from 'react';
import { Box, Button, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { SecretsHiddenView } from '../../types';

/** The credential shapes this build names (`vectors/token-patterns.json` and the egress policy's own); any other reads "secret". */
const KNOWN_KINDS: ReadonlySet<string> = new Set([
  'anthropic_key',
  'aws_access_key',
  'github_token',
  'gitlab_token',
  'google_api_key',
  'jwt',
  'openai_key',
  'pipeshub_pat',
  'private_key',
  'slack_token',
  'policy_pattern',
]);

/** Each kind as the reader names it, each name once (two unknown kinds are both "secret"). */
function kindNames(t: TFunction, kinds: readonly string[]): string[] {
  return [...new Set(kinds.map((kind) => t(`secretsHidden.kind.${KNOWN_KINDS.has(kind) ? kind : 'other'}`)))];
}

/** "a, b and c" in the reader's language. */
function listOf(names: string[], language: string): string {
  try {
    return new Intl.ListFormat(language, { style: 'long', type: 'conjunction' }).format(names);
  } catch {
    return names.join(', ');
  }
}

export interface SecretsHiddenWords {
  title: string;
  detail: string;
}

/** The line's words, or null when nothing was hidden. Kinds and a count only: the view never carries a value. */
export function secretsHiddenWords(
  t: TFunction,
  hidden: SecretsHiddenView | undefined,
  language: string,
): SecretsHiddenWords | null {
  const kinds = (hidden?.kinds ?? []).filter((kind) => typeof kind === 'string' && kind !== '');
  if (kinds.length === 0) return null;
  const count = kinds.length;
  return {
    title: t('secretsHidden.title', { count }),
    detail: t('secretsHidden.detail', { count, kinds: listOf(kindNames(t, kinds), language) }),
  };
}

/**
 * WP103-FU-1: "Hid a secret-shaped value from the model", one plain line in the run owner's activity, collapsed until
 * opened; opened, it names the kinds and says the model saw a placeholder. The timeline it reads is the owner's only.
 */
export function SecretsHiddenLine({ hidden }: { hidden: SecretsHiddenView | undefined }) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const detailId = useId();
  const words = secretsHiddenWords(t, hidden, i18n.language);
  if (words === null) return null;
  return (
    <Box data-testid="secrets-hidden" style={{ marginTop: 'var(--space-2)', borderLeft: '2px solid var(--olive-5)', paddingLeft: 'var(--space-3)' }}>
      <Button
        type="button"
        variant="ghost"
        color="gray"
        size="1"
        aria-expanded={open}
        aria-controls={detailId}
        onClick={() => setOpen((v) => !v)}
      >
        <span aria-hidden="true" style={{ display: 'inline-flex' }}>
          <MaterialIcon name={open ? 'expand_more' : 'chevron_right'} size={16} />
        </span>
        {words.title}
      </Button>
      {open ? (
        <Text as="p" id={detailId} size="1" style={{ color: 'var(--slate-11)', margin: 'var(--space-1) 0 0' }}>
          {words.detail}
        </Text>
      ) : null}
    </Box>
  );
}
