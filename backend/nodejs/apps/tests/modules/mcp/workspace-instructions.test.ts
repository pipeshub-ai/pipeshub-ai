import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as cmConfig from '../../../src/modules/configuration_manager/config/config'
import * as encryptorModule from '../../../src/libs/encryptor/encryptor'
import {
  MCP_WORKSPACE_INSTRUCTIONS_TTL_MS,
  McpWorkspaceInstructions,
  sendsInstructions,
} from '../../../src/modules/mcp/workspace-instructions'

/** One org's stored prompts, as the settings page saves them (encryption is a pass-through here). */
const storeWith = (prompts: Record<string, unknown> | null) => ({
  get: sinon.stub().callsFake(async (key: string) =>
    key === '/services/systemPrompts' && prompts ? JSON.stringify(prompts) : null,
  ),
})

describe('MCP workspace instructions', () => {
  beforeEach(() => {
    McpWorkspaceInstructions.resetCache()
    sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns({ algorithm: 'a', secretKey: 'k' } as any)
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns({
      decrypt: (v: string) => v,
      encrypt: (v: string) => v,
    } as any)
  })

  afterEach(() => {
    sinon.restore()
    McpWorkspaceInstructions.resetCache()
  })

  it('sends the Agent prompt, trimmed, when the admin switched it on', async () => {
    const store = storeWith({ customSystemPromptAgent: '  Cite sources.\n', useInMcp: true })
    expect(await McpWorkspaceInstructions.read('org-a', async () => store)).to.equal('Cite sources.')
  })

  it('sends nothing while the switch is off, or the prompt is blank, or nothing is saved', async () => {
    for (const prompts of [
      { customSystemPromptAgent: 'Cite sources.', useInMcp: false },
      { customSystemPromptAgent: 'Cite sources.' },
      { customSystemPromptAgent: '   ', useInMcp: true },
      null,
    ]) {
      McpWorkspaceInstructions.resetCache()
      expect(await McpWorkspaceInstructions.read('org-a', async () => storeWith(prompts))).to.equal(undefined)
    }
  })

  it("reads each org's prompts from that org's store", async () => {
    const stores: Record<string, ReturnType<typeof storeWith>> = {
      'org-a': storeWith({ customSystemPromptAgent: 'A rules', useInMcp: true }),
      'org-b': storeWith({ customSystemPromptAgent: 'B rules', useInMcp: true }),
    }
    const storeForOrg = sinon.stub().callsFake(async (orgId: string) => stores[orgId])
    expect(await McpWorkspaceInstructions.read('org-a', storeForOrg)).to.equal('A rules')
    expect(await McpWorkspaceInstructions.read('org-b', storeForOrg)).to.equal('B rules')
    expect(storeForOrg.args.map((a) => a[0])).to.deep.equal(['org-a', 'org-b'])
  })

  it('reads again once the cached copy is older than the TTL', async () => {
    const clock = sinon.useFakeTimers({ now: 1_000, toFake: ['Date'] })
    const store = storeWith({ customSystemPromptAgent: 'v1', useInMcp: true })
    expect(await McpWorkspaceInstructions.read('org-a', async () => store)).to.equal('v1')
    store.get.callsFake(async () => JSON.stringify({ customSystemPromptAgent: 'v2', useInMcp: true }))
    expect(await McpWorkspaceInstructions.read('org-a', async () => store)).to.equal('v1')
    clock.tick(MCP_WORKSPACE_INSTRUCTIONS_TTL_MS)
    expect(await McpWorkspaceInstructions.read('org-a', async () => store)).to.equal('v2')
  })

  it('keeps answering after a failed read: with the last text it read, else with none', async () => {
    const clock = sinon.useFakeTimers({ now: 1_000, toFake: ['Date'] })
    const store = storeWith({ customSystemPromptAgent: 'Kept', useInMcp: true })
    expect(await McpWorkspaceInstructions.read('org-a', async () => store)).to.equal('Kept')
    clock.tick(MCP_WORKSPACE_INSTRUCTIONS_TTL_MS)
    store.get.rejects(new Error('store down'))
    expect(await McpWorkspaceInstructions.read('org-a', async () => store)).to.equal('Kept')
    expect(await McpWorkspaceInstructions.read('org-b', async () => store)).to.equal(undefined)
  })

  it('sends nothing, and waits for nothing, before the store has connected', async () => {
    const store = { ...storeWith({ customSystemPromptAgent: 'Rules', useInMcp: true }), isConnected: () => false }
    expect(await McpWorkspaceInstructions.read('org-a', async () => store)).to.equal(undefined)
    expect(store.get.called).to.equal(false)
  })

  it('keeps sending the last text it read while the store is disconnected', async () => {
    const clock = sinon.useFakeTimers({ now: 1_000, toFake: ['Date'] })
    let connected = true
    const store = { ...storeWith({ customSystemPromptAgent: 'Kept', useInMcp: true }), isConnected: () => connected }
    expect(await McpWorkspaceInstructions.read('org-a', async () => store)).to.equal('Kept')
    clock.tick(MCP_WORKSPACE_INSTRUCTIONS_TTL_MS)
    connected = false
    expect(await McpWorkspaceInstructions.read('org-a', async () => store)).to.equal('Kept')
    expect(store.get.callCount).to.equal(1)
  })

  it('is needed only by the requests the server answers with its instructions', () => {
    expect(sendsInstructions({ method: 'initialize' })).to.equal(true)
    expect(sendsInstructions({ method: 'prompts/get' })).to.equal(true)
    expect(sendsInstructions([{ method: 'tools/list' }, { method: 'initialize' }])).to.equal(true)
    expect(sendsInstructions({ method: 'tools/call' })).to.equal(false)
    expect(sendsInstructions({ method: 'tools/list' })).to.equal(false)
    expect(sendsInstructions(undefined)).to.equal(false)
  })
})
