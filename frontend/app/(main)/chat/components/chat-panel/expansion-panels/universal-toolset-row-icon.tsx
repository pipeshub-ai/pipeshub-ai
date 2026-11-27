'use client';

import React, { useState } from 'react';
import Image from 'next/image';
import { ConnectorIcon, resolveConnectorType } from '@/app/components/ui/ConnectorIcon';

/** An action's logo: its icon file, else the connector icon its type or name suggests. */
export function UniversalToolsetRowIcon({
  toolsetSlug,
  label,
  iconPath,
  size = 18,
}: {
  toolsetSlug: string;
  label: string;
  iconPath?: string;
  size?: number;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  const path = iconPath?.trim();
  const pathOk = Boolean(path && (path.startsWith('/') || path.startsWith('http')));
  if (pathOk && !imgFailed) {
    return (
      <Image
        src={path!}
        alt=""
        width={size}
        height={size}
        unoptimized
        onError={() => setImgFailed(true)}
        style={{ objectFit: 'contain', flexShrink: 0 }}
      />
    );
  }
  return <ConnectorIcon type={resolveConnectorType(toolsetSlug || label)} size={size} />;
}
