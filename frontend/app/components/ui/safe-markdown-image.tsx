'use client';

import React, { useState } from 'react';
import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
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
  const isMobile = useIsMobile();
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

  return (
    <Flex
      as="span"
      direction="column"
      align="center"
      gap="2"
      data-testid="remote-image-placeholder"
      style={{
        padding: 'var(--space-4)',
        border: '1px dashed var(--slate-7)',
        borderRadius: 'var(--radius-2)',
        backgroundColor: 'var(--slate-2)',
      }}
    >
      <MaterialIcon name="image_not_supported" size={24} color="var(--slate-9)" />
      <Text size="1" as="span" style={{ color: 'var(--slate-11)' }}>
        {t('chatStream.remoteImageBlocked')}
      </Text>
      <Text size="1" as="span" style={{ color: 'var(--slate-10)', wordBreak: 'break-all' }}>
        {t('chatStream.remoteImageFrom', { host: decision.host })}
      </Text>
      <Button
        type="button"
        size="1"
        variant="soft"
        color="gray"
        style={isMobile ? { minWidth: 44, minHeight: 44 } : undefined}
        onClick={() => setUserApproved(true)}
      >
        {t('chatStream.remoteImageLoad')}
      </Button>
    </Flex>
  );
}
