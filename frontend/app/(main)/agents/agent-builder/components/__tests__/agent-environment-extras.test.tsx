/**
 * The builder's "Environment" section takes more of the environment from `AgentEnvironmentExtras` (config.ts); this
 * build's default adds nothing.
 */
import React from 'react';
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { AgentEnvironmentExtras } from '@/config';
import { AgentEnvironmentExtras as DefaultExtras } from '../agent-environment-extras';
import { draftOf } from '../../../agent-environment';

describe('AgentEnvironmentExtras', () => {
  it('is the default this build exports, and renders nothing, read-only or not', () => {
    expect(AgentEnvironmentExtras).toBe(DefaultExtras);
    const props = { draft: draftOf(null), commit: () => undefined, folderProblem: null };
    expect(render(<AgentEnvironmentExtras {...props} />).container.innerHTML).toBe('');
    expect(render(<AgentEnvironmentExtras {...props} readOnly folderProblem="x" />).container.innerHTML).toBe('');
  });
});
