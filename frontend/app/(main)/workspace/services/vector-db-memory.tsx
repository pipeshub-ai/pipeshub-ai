'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { VectorDbMemoryReport } from './api';

export type { VectorDbMemoryReport };

export const VECTOR_DB_SIZING_GUIDE_URL =
  'https://github.com/pipeshub-ai/pipeshub-ai/blob/main/docs/deployment/vector-db-sizing.md';

const GIB = 1024 ** 3;

/**
 * The server decides the threshold; the page only shows its verdict, and only
 * when both figures it quotes are present.
 */
export function shouldWarnAboutVectorDbMemory(
  report: VectorDbMemoryReport | null | undefined,
): report is VectorDbMemoryReport & { neededBytes: number; availableBytes: number } {
  return (
    report?.status === 'insufficient' &&
    typeof report.neededBytes === 'number' &&
    typeof report.availableBytes === 'number'
  );
}

export function formatGigabytes(bytes: number, locale: string): string {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 1, minimumFractionDigits: 1 }).format(
    bytes / GIB,
  );
}

export function VectorDbMemoryWarning({ report }: { report: VectorDbMemoryReport | null }) {
  const { t, i18n } = useTranslation();
  if (!shouldWarnAboutVectorDbMemory(report)) return null;

  const values = {
    needed: formatGigabytes(report.neededBytes, i18n.language),
    available: formatGigabytes(report.availableBytes, i18n.language),
  };
  const message =
    report.availableSource === 'host'
      ? t('workspace.services.vectorDbMemory.messageHost', values)
      : t('workspace.services.vectorDbMemory.message', values);

  return (
    <Flex
      role="alert"
      align="start"
      gap="3"
      style={{
        backgroundColor: 'var(--amber-2)',
        border: '1px solid var(--amber-6)',
        borderRadius: 'var(--radius-2)',
        padding: '12px 14px',
      }}
    >
      <Box style={{ flexShrink: 0, marginTop: 2 }}>
        <MaterialIcon name="warning" size={16} color="var(--amber-11)" />
      </Box>
      <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('workspace.services.vectorDbMemory.title')}
        </Text>
        <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px' }}>
          {message}
        </Text>
        <Text size="1">
          <a
            href={VECTOR_DB_SIZING_GUIDE_URL}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: 'var(--accent-11)' }}
          >
            {t('workspace.services.vectorDbMemory.guide')}
          </a>
        </Text>
      </Flex>
    </Flex>
  );
}
