export const HELP_TOOLTIP_DEFAULT_MAX_WIDTH = '300px';

export function buildHelpTooltipProps(content: string, maxWidth = HELP_TOOLTIP_DEFAULT_MAX_WIDTH) {
  if (typeof content !== 'string' || !content.trim()) {
    throw new Error('Help tooltip content must be a non-empty string');
  }

  return {
    content,
    maxWidth,
    style: { whiteSpace: 'pre-line' as const, lineHeight: 1.55 },
  };
}
