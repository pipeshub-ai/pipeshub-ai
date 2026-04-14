'use client';

import React from 'react';

interface NotFoundIconProps {
  size?: number;
  color?: string;
  style?: React.CSSProperties;
}

export const NotFoundIcon = ({ size = 56, color = 'currentColor', style }: NotFoundIconProps) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 56 56"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    style={{ display: 'inline-flex', flexShrink: 0, color, ...style }}
  >
    {/* Bordered rects - outline pixels */}
    <rect x="14.9336" y="11.1992" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="14.9336" y="7.4668" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="18.667" y="3.73242" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="22.4004" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="14.9336" y="14.9336" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="14.9336" y="18.666" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="14.9336" y="22.4004" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="14.9336" y="26.1328" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="14.9336" y="29.8672" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="18.667" y="33.5996" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="22.4004" y="37.334" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="26.1338" y="37.334" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="29.8662" y="37.334" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="33.5996" y="37.334" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="37.333" y="37.334" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="52.2666" y="22.4004" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="52.2666" y="26.1328" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="52.2666" y="29.8672" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="44.7998" y="37.334" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="48.5332" y="33.5996" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="41.0664" y="37.334" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="52.2666" y="14.9336" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="52.2666" y="18.666" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="52.2666" y="11.1992" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="37.333" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="41.0664" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="48.5332" y="3.73242" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="44.7998" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="52.2666" y="7.4668" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="33.5996" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="29.8662" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="26.1338" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="7.4668" y="44.8008" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="11.2002" y="41.0664" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="14.9336" y="37.334" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    <rect x="3.7334" y="48.5332" width="3.73333" height="3.73333" fill="none" stroke="currentColor" />
    {/* Interior filled rects */}
    <rect x="29.8662" y="14.9336" width="3.73333" height="3.73333" fill="currentColor" />
    <rect x="26.1338" y="11.1992" width="3.73333" height="3.73333" fill="currentColor" />
    <rect x="33.5996" y="18.666" width="3.73333" height="3.73333" fill="currentColor" />
    <rect x="37.333" y="22.4004" width="3.73333" height="3.73333" fill="currentColor" />
    <rect x="41.0664" y="26.1328" width="3.73333" height="3.73333" fill="currentColor" />
    <rect x="37.333" y="14.9336" width="3.73333" height="3.73333" fill="currentColor" />
    <rect x="41.0664" y="11.1992" width="3.73333" height="3.73333" fill="currentColor" />
    <rect x="29.8662" y="22.4004" width="3.73333" height="3.73333" fill="currentColor" />
    <rect x="26.1338" y="26.1328" width="3.73333" height="3.73333" fill="currentColor" />
  </svg>
);
