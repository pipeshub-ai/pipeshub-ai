'use client';

import React, { useId, useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

const cardStyle: React.CSSProperties = {
  padding: 16,
  backgroundColor: 'var(--olive-2)',
  borderRadius: 'var(--radius-2)',
  border: '1px solid var(--olive-3)',
};

const resetFieldset: React.CSSProperties = { border: 0, margin: 0, padding: 0, minWidth: 0 };

/**
 * A titled group of settings (`<fieldset>` + `<legend>`). When `collapsible`, it
 * starts closed and its header shows `summary`; `forceOpen` keeps it open (e.g. a
 * field inside has an error).
 */
export function SettingsSection({
  title,
  description,
  collapsible = false,
  summary,
  forceOpen = false,
  children,
}: {
  title: string;
  description?: string;
  collapsible?: boolean;
  summary?: string;
  forceOpen?: boolean;
  children: React.ReactNode;
}) {
  const contentId = useId();
  const [open, setOpen] = useState(!collapsible);
  const expanded = !collapsible || open || forceOpen;

  const heading = (
    <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
      {title}
    </Text>
  );

  return (
    <fieldset style={{ ...resetFieldset, ...cardStyle }}>
      <legend style={{ padding: 0, width: '100%', float: 'left' }}>
        {collapsible ? (
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={contentId}
            onClick={() => setOpen(!expanded)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              width: '100%',
              minHeight: 44,
              padding: 0,
              border: 0,
              background: 'none',
              cursor: 'pointer',
              textAlign: 'left',
              font: 'inherit',
            }}
          >
            <MaterialIcon name={expanded ? 'expand_more' : 'chevron_right'} size={20} color="var(--gray-11)" />
            <Flex direction="column" style={{ minWidth: 0 }}>
              {heading}
              {!expanded && summary ? (
                <Text size="1" style={{ color: 'var(--gray-11)', overflowWrap: 'anywhere' }}>
                  {summary}
                </Text>
              ) : null}
            </Flex>
          </button>
        ) : (
          heading
        )}
      </legend>
      <div style={{ clear: 'both' }} />
      {description ? (
        <Text as="p" size="1" style={{ color: 'var(--gray-10)', marginTop: 4 }}>
          {description}
        </Text>
      ) : null}
      <div id={contentId} hidden={!expanded}>
        <Flex direction="column" gap="4" style={{ paddingTop: 16 }}>
          {children}
        </Flex>
      </div>
    </fieldset>
  );
}
