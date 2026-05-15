'use client';

import React from 'react';
import { Text } from '@radix-ui/themes';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import type { CitationData } from './types';
import { getCitationCopyHref } from './utils';

const titleTextStyle: React.CSSProperties = {
  color: 'var(--accent-11)',
  lineHeight: 1.25,
  fontSize: 'var(--font-size-1)',
  maxWidth: 'min(240px, 100%)',
  minWidth: 0,
  overflowWrap: 'anywhere',
  wordBreak: 'break-word',
};

const linkWrapperStyle: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 'var(--space-1)',
  minWidth: 0,
  textDecoration: 'none',
  color: 'inherit',
  cursor: 'inherit',
};

function suppressPlainCitationCopyLinkClick(e: React.MouseEvent<HTMLAnchorElement>): void {
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
}

interface CitationSourceLinkRowProps {
  citation: CitationData;
  connector: string;
  truncatedName: string;
}

/**
 * Connector icon + truncated record name; wraps in `<a href>` when the
 * citation exposes a copyable `webUrl` so the browser offers **Copy link**.
 */
export function CitationSourceLinkRow({
  citation,
  connector,
  truncatedName,
}: CitationSourceLinkRowProps) {
  const copyHref = getCitationCopyHref(citation);

  const label = (
    <>
      <ConnectorIcon type={connector} size={14} />
      <Text as="span" size="1" weight="medium" style={titleTextStyle}>
        {truncatedName}
      </Text>
    </>
  );

  if (!copyHref) {
    return label;
  }

  return (
    <a
      href={copyHref}
      rel="noopener noreferrer"
      onClick={suppressPlainCitationCopyLinkClick}
      style={linkWrapperStyle}
    >
      {label}
    </a>
  );
}
