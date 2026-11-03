'use client';

import React from 'react';
import { PipesHubIcon } from './pipes-hub-icon';

export interface BrandMarkProps {
  size?: number;
  color?: string;
  style?: React.CSSProperties;
  className?: string;
  alt?: string;
  /** The page's own logo for this spot; the PipesHub icon when absent. */
  fallback?: React.ReactNode;
}

/** The product mark. Editions plug in via config.ts. */
export function BrandMark({ size = 80, color = 'currentColor', style, className, fallback }: BrandMarkProps) {
  if (fallback !== undefined) return <>{fallback}</>;
  return <PipesHubIcon size={size} color={color} style={style} className={className} />;
}
