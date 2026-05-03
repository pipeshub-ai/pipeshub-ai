'use client';

import React from 'react';

interface AlertSquareIconProps {
  size?: number;
  color?: string;
  style?: React.CSSProperties;
}

export const AlertSquareIcon = ({ size = 56, color = 'currentColor', style }: AlertSquareIconProps) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 56 56"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    style={{ display: 'inline-flex', flexShrink: 0, color, ...style }}
  >
    <rect x="10.5" y="17.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="10.5" y="35" width="3.5" height="3.5" fill="currentColor" />
    <rect x="10.5" y="21" width="3.5" height="3.5" fill="currentColor" />
    <rect x="10.5" y="31.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="10.5" y="24.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="10.5" y="28" width="3.5" height="3.5" fill="currentColor" />
    <rect x="10.5" y="14" width="3.5" height="3.5" fill="currentColor" />
    <rect x="10.5" y="38.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="14" y="10.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="14" y="42" width="3.5" height="3.5" fill="currentColor" />
    <rect x="17.5" y="10.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="17.5" y="42" width="3.5" height="3.5" fill="currentColor" />
    <rect x="21" y="10.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="21" y="42" width="3.5" height="3.5" fill="currentColor" />
    <rect x="24.5" y="10.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="24.5" y="42" width="3.5" height="3.5" fill="currentColor" />
    <rect x="28" y="10.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="28" y="42" width="3.5" height="3.5" fill="currentColor" />
    <rect x="31.5" y="10.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="31.5" y="42" width="3.5" height="3.5" fill="currentColor" />
    <rect x="35" y="10.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="35" y="42" width="3.5" height="3.5" fill="currentColor" />
    <rect x="38.5" y="10.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="38.5" y="42" width="3.5" height="3.5" fill="currentColor" />
    <rect x="42" y="14" width="3.5" height="3.5" fill="currentColor" />
    <rect x="42" y="38.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="42" y="17.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="42" y="35" width="3.5" height="3.5" fill="currentColor" />
    <rect x="42" y="21" width="3.5" height="3.5" fill="currentColor" />
    <rect x="42" y="31.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="42" y="24.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="42" y="28" width="3.5" height="3.5" fill="currentColor" />
    <rect x="26.5" y="20.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="26.5" y="24" width="3.5" height="3.5" fill="currentColor" />
    <rect x="26.5" y="27.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="26.5" y="34.5" width="3.5" height="3.5" fill="currentColor" />
    <rect x="26.5" y="17" width="3.5" height="3.5" fill="currentColor" />
  </svg>
);
