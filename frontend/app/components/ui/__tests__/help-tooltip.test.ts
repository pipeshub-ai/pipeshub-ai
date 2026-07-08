import { describe, it, expect } from 'vitest';
import { buildHelpTooltipProps, HELP_TOOLTIP_DEFAULT_MAX_WIDTH } from '../help-tooltip';

describe('buildHelpTooltipProps', () => {
  it('returns plain string content for Radix Tooltip', () => {
    const props = buildHelpTooltipProps('How manual indexing works');

    expect(typeof props.content).toBe('string');
    expect(props.content).toBe('How manual indexing works');
    expect(props.maxWidth).toBe(HELP_TOOLTIP_DEFAULT_MAX_WIDTH);
    expect(props.style).toEqual({ whiteSpace: 'pre-line', lineHeight: 1.55 });
  });

  it('does not override tooltip text color', () => {
    const props = buildHelpTooltipProps('Details');

    expect(props.style).not.toHaveProperty('color');
    expect(JSON.stringify(props)).not.toContain('gray-12');
  });

  it('supports a custom max width', () => {
    const props = buildHelpTooltipProps('Details', '240px');
    expect(props.maxWidth).toBe('240px');
  });

  it('rejects empty content', () => {
    expect(() => buildHelpTooltipProps('   ')).toThrow(/non-empty string/);
  });
});
