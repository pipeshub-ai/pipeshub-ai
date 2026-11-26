/**
 * WP-100 (C20.18 / G-17, C20.20 / G-19): "Environment" in the agent builder (mock
 * docs/agent-harness-v2/impl/mocks/wp100-environments.html, cases ui-cases/wp100-environments.json).
 *
 * - WP100-ENV-F1 (UI-ENV-1): setup commands and settings are saved on the agent node as the run spec's environment;
 *   reopening reads them back, and a stored environment's folders and shared caches are kept on an edit.
 * - WP100-ENV-F2 (UI-ENV-2): a pasted devcontainer.json saves a repository environment and names what is not used.
 * - WP100-ENV-F3 (UI-ENV-3): a setting that looks secret, or one PipesHub sets, is named and nothing is saved.
 * - WP100-ENV-F4: this build adds no folders to keep and no shared caches (`AgentEnvironmentExtras` renders nothing)
 *   and reads no cache list.
 * - WP100-ENV-F5: the save sends the edited environment, `{}` to clear a stored one, nothing for an agent without.
 * - R25UI-ENV-1 (R25FIX-FU-1): how it is set up is a radio group of cards laid out in as many columns as fit, so
 *   "From devcontainer.json" wraps inside the agent card; a segmented control never wraps and ran past the card.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';

type Node = { id: string; data: { config: Record<string, unknown> } };
const flow = vi.hoisted(() => ({ nodes: [] as Node[] }));
const api = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('@xyflow/react', () => ({
  useReactFlow: () => ({
    setNodes: (update: (nodes: Node[]) => Node[]) => {
      flow.nodes = update(flow.nodes);
    },
  }),
}));
vi.mock('@/lib/api', () => ({
  apiClient: { get: api.get },
}));

import { AgentEnvironmentSection } from '../components/agent-environment-section';
import { agentEnvironmentPayload, draftOf, environmentOf } from '../../agent-environment';
import { installBrowserShims } from './agent-builder-harness';

beforeAll(async () => {
  installBrowserShims();
  await i18n.changeLanguage('en-US');
});
beforeEach(() => {
  flow.nodes = [{ id: 'core', data: { config: { systemPrompt: 'p' } } }];
  api.get.mockReset();
});
afterEach(() => cleanup());

const saved = () => flow.nodes[0]?.data.config['environment'];

function show(stored?: unknown) {
  render(
    <Theme>
      <AgentEnvironmentSection nodeId="core" stored={stored} />
    </Theme>,
  );
}

describe("the agent's environment (C20.18)", () => {
  it('WP100-ENV-F1: steps and settings are saved as the environment; stored folders and members of an edition are kept', () => {
    show();
    fireEvent.change(screen.getByLabelText('Setup commands'), { target: { value: 'pip install -r requirements.txt' } });
    fireEvent.change(screen.getByLabelText('Settings (one per line, NAME=value)'), { target: { value: 'LANG=C.UTF-8' } });
    expect(saved()).toEqual({
      environmentId: 'agent',
      level: 'agent',
      setup: 'pip install -r requirements.txt',
      env: { LANG: 'C.UTF-8' },
    });
    cleanup();
    show(saved());
    expect((screen.getByLabelText('Setup commands') as HTMLTextAreaElement).value).toBe('pip install -r requirements.txt');
    cleanup();
    show({ environmentId: 'agent', level: 'agent', setup: 'make', cachePaths: ['/root/.cache/pip'], editionPart: ['pypi'] });
    fireEvent.change(screen.getByLabelText('Setup commands'), { target: { value: 'make all' } });
    expect(saved()).toEqual({
      environmentId: 'agent',
      level: 'agent',
      setup: 'make all',
      cachePaths: ['/root/.cache/pip'],
      editionPart: ['pypi'],
    });
  });

  it('WP100-ENV-F2: a pasted devcontainer.json is a repository environment and what is not used is named', () => {
    const stored = { environmentId: 'agent', level: 'repository', devcontainer: '{}' };
    show(stored);
    const document = '{"image": "python:3.12", "postCreateCommand": "make"}';
    fireEvent.change(screen.getByLabelText('devcontainer.json'), { target: { value: document } });
    expect(saved()).toEqual({ environmentId: 'agent', level: 'repository', devcontainer: document });
    expect(screen.getByText(/Not used: image/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('devcontainer.json'), { target: { value: '[1]' } });
    expect(screen.getByRole('alert').textContent).toBe('This is not a JSON object.');
  });

  it('WP100-ENV-F3: a secret-looking or reserved setting is named and nothing is saved', () => {
    show();
    fireEvent.change(screen.getByLabelText('Settings (one per line, NAME=value)'), {
      target: { value: 'GITHUB_TOKEN=ghp_x' },
    });
    expect(screen.getByRole('alert').textContent).toContain('GITHUB_TOKEN looks like a secret');
    expect(saved()).toBeUndefined();
    expect(environmentOf({ ...draftOf(null), settings: 'PATH=/x' })).toEqual({
      problem: { key: 'agentBuilder.environment.problem.reserved', name: 'PATH' },
    });
  });

  it('WP100-ENV-F4: no folders to keep or shared caches here, and no cache list is read', () => {
    show();
    expect(screen.queryByLabelText('Folders to keep (one per line)')).toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByText(/shared caches/i)).toBeNull();
    expect(api.get).not.toHaveBeenCalled();
  });

  it('WP100-ENV-F5: the save sends the edit, {} to clear a stored one, nothing for an agent without one', () => {
    const environment = { environmentId: 'agent', level: 'agent' as const, setup: 'make' };
    expect(agentEnvironmentPayload(environment, null)).toEqual({ environment });
    expect(agentEnvironmentPayload(undefined, environment)).toEqual({ environment: {} });
    expect(agentEnvironmentPayload(undefined, undefined)).toEqual({});
    expect(environmentOf(draftOf(null))).toEqual({ environment: null });
  });

  it('R25UI-ENV-1: the way it is set up is a radio group whose choices wrap within the card', () => {
    show();
    const how = screen.getByRole('radiogroup', { name: 'How the environment is set up' });
    expect(how.className).not.toMatch(/SegmentedControl/);
    expect(how.style.getPropertyValue('--grid-template-columns')).toBe('repeat(auto-fit, minmax(8rem, 1fr))');
    const devcontainer = screen.getByRole('radio', { name: 'From devcontainer.json' });
    expect(screen.getByRole('radio', { name: 'Setup steps' }).getAttribute('aria-checked')).toBe('true');
    fireEvent.click(devcontainer);
    expect(devcontainer.getAttribute('aria-checked')).toBe('true');
    expect(screen.getByLabelText('devcontainer.json')).toBeTruthy();
  });
});
