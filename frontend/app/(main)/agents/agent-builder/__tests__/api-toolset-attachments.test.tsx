import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, renderHook, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from './agent-builder-harness';
import { toolset } from '../../../workspace/api-toolsets/__tests__/fixtures';
import type { AgentDetail } from '../../types';

const api = vi.hoisted(() => ({ listInstances: vi.fn() }));
vi.mock('../../../workspace/api-toolsets/api', () => ({ ApiToolsetsApi: api }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { attachedToolNames, changedAttachments, toggleAttachment } from '../api-toolset-attachments';
import { extractAgentConfigFromFlow } from '../extract-agent-config';
import { useAgentBuilderReconstruction } from '../hooks/use-flow-reconstruction';
import { AgentBuilderApiToolsetsSection } from '../components/sidebar-api-toolsets-section';

const A = 'a'.repeat(32);
const B = 'b'.repeat(32);
const orders = toolset({ id: A, isConnected: true });

beforeEach(() => {
  installBrowserShims();
  api.listInstances.mockReset();
});
afterEach(cleanup);

/** Opens the first collapsed toolset row. */
const expandNext = () => fireEvent.click(screen.getAllByRole('button', { expanded: false })[0]);

describe('attachment list', () => {
  it('reads the tool names attached from one toolset', () => {
    expect(attachedToolNames([`${A}:listOrders`, `${B}:x`], A)).toEqual(new Set(['listOrders']));
  });

  it('attaches and detaches, dropping entries for toolsets or operations that no longer exist', () => {
    const stale = [`${B}:gone`, `${A}:removedOp`];
    expect(toggleAttachment(stale, [orders], A, 'listOrders', true)).toEqual([`${A}:listOrders`]);
    expect(toggleAttachment([`${A}:listOrders`, `${A}:createOrder`], [orders], A, 'listOrders', false)).toEqual([
      `${A}:createOrder`,
    ]);
    expect(toggleAttachment([], [orders], A, 'notOffered', true)).toEqual([]);
  });

  it('is sent only when it differs from what the agent has stored', () => {
    expect(changedAttachments([`${A}:x`], [`${A}:x`])).toEqual({});
    expect(changedAttachments(undefined, [`${A}:x`])).toEqual({});
    expect(changedAttachments([], [`${A}:x`])).toEqual({ apiToolsetTools: [] });
    expect(changedAttachments([`${A}:x`], undefined)).toEqual({ apiToolsetTools: [`${A}:x`] });
  });
});

describe('saving and restoring an agent', () => {
  const core = (config: Record<string, unknown>) => ({ id: 'agent-core-1', data: { type: 'agent-core', config } });

  it('sends the list from the agent core on create and only changes on update', () => {
    const created = extractAgentConfigFromFlow('A', [core({ apiToolsetTools: [`${A}:listOrders`] })], [], null);
    expect(created.apiToolsetTools).toEqual([`${A}:listOrders`]);

    const stored = { apiToolsetTools: [`${A}:listOrders`] } as AgentDetail;
    expect(extractAgentConfigFromFlow('A', [core({ apiToolsetTools: [`${A}:listOrders`] })], [], stored)).not.toHaveProperty(
      'apiToolsetTools',
    );
    expect(extractAgentConfigFromFlow('A', [core({ apiToolsetTools: [] })], [], stored).apiToolsetTools).toEqual([]);
  });

  it("puts the agent's stored list on the rebuilt agent core", () => {
    const { result } = renderHook(() => useAgentBuilderReconstruction());
    const agent = {
      name: 'A',
      toolsets: [],
      knowledge: [],
      models: [],
      apiToolsetTools: [`${A}:listOrders`],
    } as unknown as AgentDetail;
    const { nodes } = result.current.reconstructFlowFromAgent(agent, [], [], []);
    const coreNode = nodes.find((n) => n.data.type === 'agent-core');
    expect(coreNode?.data.config?.apiToolsetTools).toEqual([`${A}:listOrders`]);
  });
});

describe('AgentBuilderApiToolsetsSection', () => {
  const renderSection = (props: Partial<React.ComponentProps<typeof AgentBuilderApiToolsetsSection>> = {}) => {
    const onChange = vi.fn();
    renderInTheme(
      <AgentBuilderApiToolsetsSection attached={[]} onChange={onChange} isServiceAccount={false} structureLocked={false} {...props} />,
    );
    return onChange;
  };

  it('attaches an operation when its box is ticked', async () => {
    api.listInstances.mockResolvedValue([orders]);
    const onChange = renderSection();
    await screen.findByText('Orders');
    expandNext();
    fireEvent.click(screen.getByText('/createOrder').closest('label')!.querySelector('[role="checkbox"]')!);
    expect(onChange).toHaveBeenCalledWith([`${A}:createOrder`]);
  });

  it('says when a toolset is not connected', async () => {
    api.listInstances.mockResolvedValue([toolset({ id: A })]);
    renderSection({ attached: [`${A}:listOrders`] });
    expect(await screen.findByText(/Not connected\. The agent skips this toolset/)).toBeTruthy();
  });

  it('blocks per-member toolsets on a service-account agent but allows shared ones', async () => {
    api.listInstances.mockResolvedValue([toolset({ id: A, name: 'Mine' }), toolset({ id: B, name: 'Shared', useSharedCredential: true })]);
    renderSection({ isServiceAccount: true });
    await screen.findByText('Mine');
    expandNext();
    expect(screen.getByText('Service-account agents can only use toolsets with a shared credential.')).toBeTruthy();
    const mineBoxes = screen.getAllByRole('checkbox');
    expect(mineBoxes.every((box) => box.hasAttribute('disabled'))).toBe(true);
    expandNext();
    expect(screen.getAllByRole('checkbox').some((box) => !box.hasAttribute('disabled'))).toBe(true);
  });

  it('says so when the organization has no API toolsets', async () => {
    api.listInstances.mockResolvedValue([]);
    renderSection();
    expect(await screen.findByText(/No API toolsets yet/)).toBeTruthy();
  });
});
