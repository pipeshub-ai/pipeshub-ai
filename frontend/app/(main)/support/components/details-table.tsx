'use client';

import { useState, type CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { RecentError } from '@/lib/support/recent-errors';
import type { SupportRow } from '../types';

const DETAILS_ID = 'support-sent-details';

const cell: CSSProperties = {
  padding: '8px 12px',
  fontSize: 13,
  textAlign: 'left',
  verticalAlign: 'top',
};

/** The details sent with every request, shown before sending so nothing is shared silently. */
export function DetailsTable({
  summary,
  rows,
  recentErrors,
  includeErrors,
  onIncludeErrorsChange,
  describeError,
}: {
  /** One sentence naming what is sent; the rows open from it. */
  summary: string;
  rows: SupportRow[];
  recentErrors: RecentError[];
  includeErrors: boolean;
  onIncludeErrorsChange: (include: boolean) => void;
  describeError: (error: RecentError) => string;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const rowBorder = (index: number): CSSProperties =>
    index > 0 ? { borderTop: '1px solid var(--olive-a4)' } : {};

  return (
    <Flex direction="column" gap="3">
      <Flex align="start" gap="2">
        <MaterialIcon name="verified_user" size={16} color="var(--accent-11)" style={{ marginTop: 2 }} />
        <Text as="p" style={{ margin: 0, flex: 1, minWidth: 0, fontSize: 13, lineHeight: '20px', color: 'var(--slate-11)' }}>
          {summary}
        </Text>
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={DETAILS_ID}
          onClick={() => setExpanded((value) => !value)}
          style={{
            appearance: 'none',
            flexShrink: 0,
            padding: 0,
            border: 'none',
            background: 'transparent',
            cursor: 'pointer',
            font: 'inherit',
            fontSize: 13,
            lineHeight: '20px',
            fontWeight: 600,
            color: 'var(--accent-11)',
          }}
        >
          {expanded ? t('support.details.hideDetails') : t('support.details.viewDetails')}
        </button>
      </Flex>
      {expanded && (
        <Box
          id={DETAILS_ID}
          style={{
            borderRadius: 'var(--radius-3)',
            border: '1px solid var(--olive-a4)',
            background: 'var(--gray-a2)',
          }}
        >
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <tbody>
              {rows.map((row, index) => (
                <tr key={row.label}>
                  <th
                    scope="row"
                    style={{ ...cell, ...rowBorder(index), width: '34%', fontWeight: 500, color: 'var(--slate-11)' }}
                  >
                    {row.label}
                  </th>
                  <td style={{ ...cell, ...rowBorder(index), color: 'var(--slate-12)', overflowWrap: 'anywhere' }}>
                    {row.value}
                  </td>
                </tr>
              ))}
              {recentErrors.length > 0 && (
                <tr>
                  <th
                    scope="row"
                    style={{ ...cell, ...rowBorder(rows.length), width: '34%', fontWeight: 500, color: 'var(--slate-11)' }}
                  >
                    {t('support.details.rows.recentErrors')}
                  </th>
                  <td style={{ ...cell, ...rowBorder(rows.length), color: 'var(--slate-12)' }}>
                    <Flex asChild align="center" gap="2">
                      <label>
                        <input
                          type="checkbox"
                          checked={includeErrors}
                          onChange={(event) => onIncludeErrorsChange(event.target.checked)}
                          style={{ margin: 0, accentColor: 'var(--accent-9)' }}
                        />
                        <Text size="1">{t('support.details.includeErrors', { count: recentErrors.length })}</Text>
                      </label>
                    </Flex>
                    {includeErrors && (
                      <ul style={{ margin: '6px 0 0', paddingLeft: 16, display: 'grid', gap: 2 }}>
                        {recentErrors.map((error) => (
                          <li key={`${error.at}-${error.path}`} style={{ color: 'var(--slate-11)' }}>
                            <Text size="1">{describeError(error)}</Text>{' '}
                            {error.requestId && (
                              <Text size="1" style={{ fontFamily: 'var(--code-font-family)', color: 'var(--slate-11)' }}>
                                {error.requestId}
                              </Text>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </Box>
      )}
    </Flex>
  );
}
