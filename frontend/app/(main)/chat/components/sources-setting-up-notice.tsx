'use client';

import React, { useEffect, useState } from 'react';
import { Callout, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorsApi } from '@/app/(main)/workspace/connectors/api';
import type { Connector } from '@/app/(main)/workspace/connectors/types';

// A first sync takes minutes to hours; checking every few minutes is plenty.
const RECHECK_MS = 3 * 60_000;

async function sourcesSettingUp(): Promise<string[]> {
  const lists = await Promise.all(
    (['team', 'personal'] as const).map((scope) =>
      ConnectorsApi.getActiveConnectors(scope, 1, 100, { suppressErrorToast: true })
        .then((res) => res.connectors ?? [])
        .catch(() => [] as Connector[]),
    ),
  );
  const names = lists
    .flat()
    .filter((c) => c.awaitingFirstSync && c.isActive)
    .map((c) => c.name?.trim() || c.type);
  return [...new Set(names)];
}

/**
 * Tells anyone asking questions that a source is still being set up, so a thin
 * answer is not read as "PipesHub doesn't know". Shows only while it applies.
 */
export function SourcesSettingUpNotice({ style }: { style?: React.CSSProperties }) {
  const { t } = useTranslation();
  const [names, setNames] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    let timeoutId: number | undefined;
    const check = async () => {
      const found = await sourcesSettingUp();
      if (cancelled) return;
      setNames(found);
      // Also after an empty answer: a source added, or a failed read, shows on the next check.
      timeoutId = window.setTimeout(() => void check(), RECHECK_MS);
    };
    void check();
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, []);

  if (!names.length) return null;
  return (
    <Callout.Root color="gray" variant="surface" size="1" style={{ width: '100%', ...style }}>
      <Callout.Icon>
        <MaterialIcon name="hourglass_top" size={16} />
      </Callout.Icon>
      <Text size="2">
        {t('chat.sourcesSettingUp', { sources: names.join(', ') })}
      </Text>
    </Callout.Root>
  );
}
