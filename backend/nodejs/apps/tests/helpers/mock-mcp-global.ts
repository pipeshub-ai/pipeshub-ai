/**
 * Global MCP mock — loaded via .mocharc.yaml `require` BEFORE any test file.
 *
 * Mocks three packages used by the MCP controller:
 *   1. @pipeshub-ai/mcp/esm/mcp-server/server.js  (ESM-only — would ERR_REQUIRE_ESM)
 *   2. @pipeshub-ai/mcp/esm/core.js                (ESM-only — would ERR_REQUIRE_ESM)
 *   3. @modelcontextprotocol/sdk/server/streamableHttp.js  (needs mock for unit tests)
 *
 * Tests can mutate the exports on the cached modules to override behaviour
 * (e.g. make createMCPServer throw) — the controller destructures from the
 * same object reference on every request.
 */

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------
function patchRequireCache(resolvedPath: string, exports: any) {
  // Force-load the real module first (so require.cache has an entry),
  // then overwrite its exports.
  try { require(resolvedPath); } catch { /* ESM-only, expected */ }

  const cached = require.cache[resolvedPath];
  if (cached) {
    cached.exports = exports;
  } else {
    require.cache[resolvedPath] = {
      id: resolvedPath,
      filename: resolvedPath,
      loaded: true,
      exports,
      children: [],
      paths: [],
      parent: null,
    } as any;
  }
}

// ---------------------------------------------------------------------------
// 1. @pipeshub-ai/mcp  — createMCPServer
// ---------------------------------------------------------------------------
const mcpServerPath = require.resolve('@pipeshub-ai/mcp/esm/mcp-server/server.js');

const mcpServerExports = {
  createMCPServer: () => ({
    server: {
      connect: () => Promise.resolve(),
      close: () => Promise.resolve(),
    },
  }),
};

patchRequireCache(mcpServerPath, mcpServerExports);

// ---------------------------------------------------------------------------
// 2. @pipeshub-ai/mcp  — PipeshubCore
// ---------------------------------------------------------------------------
const mcpCorePath = require.resolve('@pipeshub-ai/mcp/esm/core.js');

const mcpCoreExports = {
  PipeshubCore: class FakePipeshubCore {
    constructor(_opts?: any) {}
  },
};

patchRequireCache(mcpCorePath, mcpCoreExports);

// ---------------------------------------------------------------------------
// 3. @modelcontextprotocol/sdk — StreamableHTTPServerTransport
// ---------------------------------------------------------------------------
const sdkTransportPath = require.resolve(
  '@modelcontextprotocol/sdk/server/streamableHttp.js',
);

class FakeStreamableHTTPServerTransport {
  opts: any;
  constructor(opts?: any) {
    this.opts = opts;
  }
  handleRequest(_req: any, _res: any, _body: any) {
    return Promise.resolve();
  }
}

const sdkTransportExports = {
  StreamableHTTPServerTransport: FakeStreamableHTTPServerTransport,
};

patchRequireCache(sdkTransportPath, sdkTransportExports);
