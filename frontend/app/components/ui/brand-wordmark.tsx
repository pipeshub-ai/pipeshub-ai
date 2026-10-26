'use client';

import React from 'react';
import Image from 'next/image';

export interface BrandWordmarkProps {
  width?: number;
  height?: number;
  style?: React.CSSProperties;
}

/** The product wordmark on the sign-in hero. Editions plug in via config.ts. */
export function BrandWordmark({ width = 170, height = 45, style }: BrandWordmarkProps) {
  return (
    <Image
      src="/login-page-assets/pipeshub/white-horizontal.svg"
      alt="Pipeshub"
      width={width}
      height={height}
      style={{ opacity: 0.95, ...style }}
    />
  );
}
