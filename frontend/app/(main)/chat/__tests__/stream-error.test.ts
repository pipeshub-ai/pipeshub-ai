import { describe, expect, it } from 'vitest';
import {
  ChatStreamError,
  MCP_SERVER_CONFIG_MISSING,
  mcpConfigMissingDetails,
  streamErrorInfo,
} from '../stream-error';

describe('streamErrorInfo', () => {
  it('keeps the code and details of a server error', () => {
    expect(streamErrorInfo(new ChatStreamError('x', 'mcp_server_config_missing', { servers: [] }))).toEqual({
      code: 'mcp_server_config_missing',
      details: { servers: [] },
    });
  });

  it('has nothing for an error without a code', () => {
    expect(streamErrorInfo(new ChatStreamError('x'))).toBeUndefined();
    expect(streamErrorInfo(new Error('x'))).toBeUndefined();
  });
});

describe('mcpConfigMissingDetails', () => {
  const server = { instanceId: 'inst-1', name: 'GitHub', problem: 'not_connected', authMode: 'oauth', sharedCredential: false };

  it('reads what the backend sends', () => {
    expect(
      mcpConfigMissingDetails({
        code: MCP_SERVER_CONFIG_MISSING,
        details: { agentId: 'a-1', serviceAccount: false, servers: [server] },
      }),
    ).toEqual({ agentId: 'a-1', serviceAccount: false, servers: [server] });
  });

  it('ignores other errors', () => {
    expect(mcpConfigMissingDetails({ code: 'stream_error', details: { servers: [server] } })).toBeNull();
    expect(mcpConfigMissingDetails(undefined)).toBeNull();
  });

  it('does not trust the shape', () => {
    expect(mcpConfigMissingDetails({ code: MCP_SERVER_CONFIG_MISSING })).toBeNull();
    expect(mcpConfigMissingDetails({ code: MCP_SERVER_CONFIG_MISSING, details: { servers: 'x' } })).toBeNull();
    expect(
      mcpConfigMissingDetails({
        code: MCP_SERVER_CONFIG_MISSING,
        details: { servers: [null, { name: 'no id' }, { instanceId: 'i', problem: 'exploded' }] },
      }),
    ).toBeNull();
  });

  it('drops bad fields and keeps the server', () => {
    const parsed = mcpConfigMissingDetails({
      code: MCP_SERVER_CONFIG_MISSING,
      details: {
        serviceAccount: 'yes',
        servers: [{ instanceId: 'inst-2', problem: 'not_found', authMode: 'telepathy', sharedCredential: 1 }],
      },
    });
    expect(parsed).toEqual({
      agentId: undefined,
      serviceAccount: false,
      servers: [{ instanceId: 'inst-2', name: 'inst-2', problem: 'not_found', authMode: undefined, sharedCredential: false }],
    });
  });
});
