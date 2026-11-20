import 'reflect-metadata'
import { expect } from 'chai'
import path from 'path'
import { MCP_PROMPT_MAX_CHARS } from '../../src/modules/configuration_manager/utils/system-prompts'

/**
 * The settings page refuses an Agent prompt longer than MCP_PROMPT_MAX_CHARS
 * while it is sent to MCP clients. The @pipeshub-ai/mcp package sends at most
 * MAX_CUSTOM_INSTRUCTIONS_CHARS of it, the length its own instructions leave
 * room for. If the two differ, an admin's saved prompt is cut on its way to
 * the client, or a prompt that would fit is refused.
 */
const nm = (p: string): string => path.join(__dirname, '../../node_modules', p)
// The mocha setup replaces the CJS view of this ESM package with a mock, so
// load the real file with a native dynamic import.
const nativeImport = new Function('p', 'return import(p)') as (p: string) => Promise<any>

describe('MCP workspace instructions limit', () => {
  it('is the length the @pipeshub-ai/mcp package sends', async () => {
    const { MAX_CUSTOM_INSTRUCTIONS_CHARS } = await nativeImport(
      nm('@pipeshub-ai/mcp/esm/mcp-server/instructions.js'),
    )
    expect(MCP_PROMPT_MAX_CHARS).to.equal(MAX_CUSTOM_INSTRUCTIONS_CHARS)
  })
})
