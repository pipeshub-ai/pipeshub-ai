'use client';

import React from 'react';
import { Tooltip } from '@radix-ui/themes';
import { buildHelpTooltipProps } from './help-tooltip';

export type HelpTooltipProps = {
  /** Plain text only — Radix tooltips set their own text/background colors. */
  content: string;
  maxWidth?: string;
  children: React.ReactNode;
};

export function HelpTooltip({ content, maxWidth, children }: HelpTooltipProps) {
  const tooltipProps = buildHelpTooltipProps(content, maxWidth);

  return <Tooltip {...tooltipProps}>{children}</Tooltip>;
}
