/**
 * The agent card's "How it runs" group takes extra settings from `AgentRuntimeExtras` (config.ts); this build's
 * default adds none.
 */
import React from 'react';
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { AgentRuntimeExtras } from '@/config';
import { AgentRuntimeExtras as DefaultExtras } from '../agent-runtime-extras';

describe('AgentRuntimeExtras', () => {
  it('is the default this build exports, and renders nothing, read-only or not', () => {
    expect(AgentRuntimeExtras).toBe(DefaultExtras);
    expect(render(<AgentRuntimeExtras />).container.innerHTML).toBe('');
    expect(render(<AgentRuntimeExtras readOnly />).container.innerHTML).toBe('');
  });
});
