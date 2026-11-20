import 'reflect-metadata'
import { expect } from 'chai'
import { execFileSync } from 'child_process'
import path from 'path'
import { pathToFileURL } from 'url'
import { MCP_PROMPT_MAX_CHARS } from '../../src/modules/configuration_manager/utils/system-prompts'

/**
 * The settings page refuses an Agent prompt longer than MCP_PROMPT_MAX_CHARS
 * while it is sent to MCP clients. The @pipeshub-ai/mcp package sends at most
 * MAX_CUSTOM_INSTRUCTIONS_CHARS of it, the length its own instructions leave
 * room for. If the two differ, an admin's saved prompt is cut on its way to
 * the client, or a prompt that would fit is refused.
 */
const instructionsModule = pathToFileURL(
  path.join(__dirname, '../../node_modules/@pipeshub-ai/mcp/esm/mcp-server/instructions.js'),
).href

describe('MCP workspace instructions limit', () => {
  it('is the length the @pipeshub-ai/mcp package sends', () => {
    // The mocha setup replaces the CJS view of this ESM package with a mock, so
    // read the real module's constant in a separate Node process.
    const sent = execFileSync(
      process.execPath,
      [
        '--input-type=module',
        '-e',
        `const m = await import(${JSON.stringify(instructionsModule)}); console.log(JSON.stringify(m.MAX_CUSTOM_INSTRUCTIONS_CHARS ?? null))`,
      ],
      { encoding: 'utf8' },
    ).trim()
    expect(MCP_PROMPT_MAX_CHARS).to.equal(JSON.parse(sent))
  })
})
