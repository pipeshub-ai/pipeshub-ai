/**
 * CPD-11 U5 (MCP-E2E F7, builder half): a tool no admin approved is listed
 * with its reason and never added when its server is dragged onto the
 * canvas; one changed since approval still is. A save the server refuses
 * (`mcp_tool_unapproved`) shows the server's own sentence.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AxiosError, AxiosHeaders } from 'axios';
import { i18n } from '@/lib/i18n';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import type { McpMyServerEntry } from '../../../workspace/mcp-servers/types';
import { buildMcpServerDragPayload, isAttachableMcpTool } from '../sidebar-mcp-utils';
import { AgentBuilderMcpSection } from '../components/sidebar-mcp-section';

const tool = (name: string, status?: 'approved' | 'unapproved' | 'changed' | 'unknown') => ({
  name,
  namespacedName: `linear__${name}`,
  description: name,
  inputSchema: {},
  ...(status ? { approval: { status, reasonCode: status === 'approved' ? null : `mcp_tool_${status}` } } : {}),
});

const entry = (tools: ReturnType<typeof tool>[]): McpMyServerEntry =>
  ({
    _id: 'inst-1',
    name: 'Linear',
    typeId: 'linear',
    isAuthenticated: true,
    authMode: 'none',
    isCustom: false,
    tools,
  }) as unknown as McpMyServerEntry;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

describe('U5 unapproved MCP tools in the agent builder', () => {
  it('an unapproved tool is not attachable; approved, changed, unknown and hub-off tools are', () => {
    expect(isAttachableMcpTool(tool('a', 'unapproved'))).toBe(false);
    for (const status of ['approved', 'changed', 'unknown'] as const) expect(isAttachableMcpTool(tool('a', status))).toBe(true);
    expect(isAttachableMcpTool(tool('a'))).toBe(true);
  });

  it('dragging the server adds only the attachable tools', () => {
    const payload = buildMcpServerDragPayload(
      entry([tool('list_issues', 'approved'), tool('create_issue', 'unapproved'), tool('get_issue', 'changed')]),
    );
    expect(JSON.parse(payload.tools).map((t: { name: string }) => t.name)).toEqual(['list_issues', 'get_issue']);
    expect(payload.toolCount).toBe('2');
  });

  it('lists each tool with its approval note; the unapproved one is shown disabled', () => {
    render(
      <Theme>
        <AgentBuilderMcpSection
          mcpServers={[entry([tool('list_issues', 'approved'), tool('create_issue', 'unapproved'), tool('get_issue', 'changed')])]}
          loading={false}
          refreshMcpServers={vi.fn()}
          mcpMergeCheckNodes={[]}
          onNotify={vi.fn()}
        />
      </Theme>,
    );
    act(() => (document.querySelector('[aria-expanded]') as HTMLElement).click());
    const unapproved = document.querySelector('[data-mcp-tool="create_issue"]') as HTMLElement;
    expect(unapproved.getAttribute('aria-disabled')).toBe('true');
    expect(unapproved.textContent).toContain("Not approved: an admin hasn't approved this tool");
    expect((document.querySelector('[data-mcp-tool="get_issue"]') as HTMLElement).textContent).toContain(
      'Changed since an admin approved it',
    );
    const approved = document.querySelector('[data-mcp-tool="list_issues"]') as HTMLElement;
    expect(approved.getAttribute('aria-disabled')).toBeNull();
    expect(approved.textContent).not.toContain('approve');
  });

  it("a save refused with mcp_tool_unapproved shows the server's sentence", () => {
    const error = new AxiosError('Request failed with status code 400');
    const message =
      'These MCP tools are not approved by an admin yet and cannot be attached: Linear: create_issue. An admin approves a server’s tools on its MCP server page.';
    error.response = {
      status: 400,
      statusText: '',
      data: { detail: { reasonCode: 'mcp_tool_unapproved', message, tools: { 'inst-1': ['create_issue'] } } },
      headers: new AxiosHeaders(),
      config: { headers: new AxiosHeaders() },
    } as never;
    expect(getUserFacingErrorMessage(error, 'fallback')).toBe(message);
  });
});
