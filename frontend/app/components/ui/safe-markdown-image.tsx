'use client';

import React, { useState } from 'react';
import { Button } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { classifyImageUrl } from '@/lib/utils/image-url-policy';

interface SafeMarkdownImageProps {
  src?: string | Blob;
  alt?: string;
  style?: React.CSSProperties;
  width?: number | string;
  height?: number | string;
}

/**
 * `<img>` for Markdown/HTML that a model or an indexed document produced. Only
 * URLs `classifyImageUrl` trusts load on render; any other host shows a
 * placeholder naming it, and loads only after the user clicks "Load image".
 */
export function SafeMarkdownImage({ src, alt, style, width, height }: SafeMarkdownImageProps) {
  const { t } = useTranslation();
  const [userApproved, setUserApproved] = useState(false);
  const decision = classifyImageUrl(typeof src === 'string' ? src : undefined);

  if (decision.kind === 'blocked') {
    return alt ? <span style={{ fontStyle: 'italic' }}>{alt}</span> : null;
  }

  if (decision.kind === 'auto' || userApproved) {
    return (
      <img
        src={decision.src}
        alt={alt ?? ''}
        width={width}
        height={height}
        loading="lazy"
        referrerPolicy="no-referrer"
        style={style}
      />
    );
  }

  const { host } = decision;
  return (
    <span
      data-testid="remote-image-placeholder"
      title={decision.src}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: 'var(--space-2)',
        maxWidth: '100%',
        padding: 'var(--space-2) var(--space-3)',
        border: '1px dashed var(--slate-7)',
        borderRadius: 'var(--radius-2)',
        background: 'var(--slate-2)',
        color: 'var(--slate-11)',
        fontSize: 'var(--font-size-1)',
        verticalAlign: 'middle',
      }}
    >
      <span aria-hidden="true" style={{ display: 'inline-flex' }}>
        <MaterialIcon name="image_not_supported" size={16} />
      </span>
      <span style={{ overflowWrap: 'anywhere' }}>
        {t('chat.remoteImage.notLoaded', { host, defaultValue: 'Image from {{host}} not loaded' })}
      </span>
      <Button
        type="button"
        size="1"
        variant="soft"
        color="gray"
        aria-label={t('chat.remoteImage.loadFromHost', { host, defaultValue: 'Load image from {{host}}' })}
        onClick={() => setUserApproved(true)}
      >
        {t('chat.remoteImage.load', { defaultValue: 'Load image' })}
      </Button>
    </span>
  );
}
