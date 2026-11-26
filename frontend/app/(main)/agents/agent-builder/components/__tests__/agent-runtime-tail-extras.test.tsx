/**
 * The agent card's "How it runs" group ends, after its schedules, with `AgentRuntimeTailExtras` (config.ts); this
 * build's default adds nothing.
 */
import React from 'react';
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { AgentRuntimeTailExtras } from '../agent-runtime-tail-extras';

describe('AgentRuntimeTailExtras', () => {
  it('renders nothing by default, read-only or not', () => {
    expect(render(<AgentRuntimeTailExtras />).container.innerHTML).toBe('');
    expect(render(<AgentRuntimeTailExtras readOnly />).container.innerHTML).toBe('');
  });
});
