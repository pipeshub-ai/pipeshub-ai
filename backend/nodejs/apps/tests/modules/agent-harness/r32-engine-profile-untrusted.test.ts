/**
 * R32-TRUST (ADR-SEC-01 D7): an ACP engine whose image is there but whose profile is signed by no key this
 * deployment trusts is named that way. The broker lists it under `enginesUntrusted` as well as `enginesUnavailable`;
 * the pickers grey it with `ENGINE_UNAVAILABLE` and the reason `engine_profile_untrusted` (an admin sees what to
 * fix), and a save is refused with "This engine's profile isn't signed for this deployment", never "not installed".
 */
import { expect } from 'chai';
import { HttpEngineAvailability } from '../../../src/modules/agent-harness/adapters/http/http-engine-availability';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import {
  CodingEngineService,
  ENGINE_NOT_INSTALLED_MESSAGE,
  ENGINE_PROFILE_UNTRUSTED,
  ENGINE_PROFILE_UNTRUSTED_MESSAGE,
} from '../../../src/modules/agent-harness/application/coding-engine-service';
import { codingEngineViewSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/coding-engine-view';
import { engineRef, isAcpKind, RELEASE_ENGINE_PROFILES } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import type { EngineAvailability } from '../../../src/modules/agent-harness/ports/engine-availability';
import { testControlPlane } from '../control-plane/support/in-memory';
import { testModelAccess } from './support/model-access';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const ORG = 'org-1';
const USER = 'user-1';
const ACP = RELEASE_ENGINE_PROFILES.filter((p) => isAcpKind(p.engine.kind)).map((p) => p.engine);
const NATIVE = [...new Set(RELEASE_ENGINE_PROFILES.map((p) => p.engine.kind))].filter((k) => !isAcpKind(k));
const ACP_REFS = new Set(ACP.map(engineRef));

describe('R32-TRUST: an engine whose profile is not signed for this deployment says so', () => {
  let settings: FakeEngineCeilings;

  const service = (availability: EngineAvailability): CodingEngineService =>
    new CodingEngineService({
      ceilings: settings,
      access: testModelAccess(),
      preferences: new InMemoryCodingEnginePreferences(),
      transaction: testControlPlane(),
      availability,
    });

  beforeEach(async () => {
    settings = new FakeEngineCeilings();
    await settings.write(ORG, { engines: [...NATIVE, ...ACP.map((e) => e.kind)] }, 'p_admin');
  });

  it('T-1: the pickers list each untrusted ACP engine greyed with its reason, not absent', async () => {
    const listed = await service({
      unavailable: async () => ACP_REFS,
      untrusted: async () => ACP_REFS,
    }).engines(ORG);
    for (const engine of ACP) {
      const shown = listed.find((e) => e.kind === engine.kind);
      expect(shown?.available).to.equal(false);
      expect(shown?.reason).to.deep.equal({ code: 'ENGINE_UNAVAILABLE', reasonCode: ENGINE_PROFILE_UNTRUSTED });
    }
    expect(listed.filter((e) => !isAcpKind(e.kind)).every((e) => e.available)).to.equal(true);
    const view = await service({ unavailable: async () => ACP_REFS, untrusted: async () => ACP_REFS }).view(ORG, USER);
    expect(codingEngineViewSchema.safeParse(view).success).to.equal(true);
  });

  it('T-2: a save is refused in plain words, never "not installed"', async () => {
    const refused = await service({ unavailable: async () => ACP_REFS, untrusted: async () => ACP_REFS })
      .setChoice(ORG, USER, ACP[0]!.kind)
      .catch((error: unknown) => error);
    expect(refused).to.be.instanceOf(HarnessConflictError);
    expect((refused as HarnessConflictError).code).to.equal('ENGINE_UNAVAILABLE');
    expect((refused as HarnessConflictError).reasonCode).to.equal(ENGINE_PROFILE_UNTRUSTED);
    expect((refused as Error).message).to.equal("This engine's profile isn't signed for this deployment");
    expect(ENGINE_PROFILE_UNTRUSTED_MESSAGE).to.not.include('installed');
  });

  it('T-3: an engine with no image and no untrusted profile is still "not installed", and an absent ACP one absent', async () => {
    const codex = RELEASE_ENGINE_PROFILES.find((p) => p.engine.kind === 'codex')!.engine;
    const missing = new Set([engineRef(codex), ...ACP_REFS]);
    const listed = await service({ unavailable: async () => missing, untrusted: async () => new Set() }).engines(ORG);
    expect(listed.find((e) => e.kind === 'codex')?.reason).to.deep.equal({ code: 'ENGINE_UNAVAILABLE' });
    expect(listed.map((e) => e.kind).filter(isAcpKind)).to.deep.equal([]);
    const refused = await service({ unavailable: async () => missing, untrusted: async () => new Set() })
      .setChoice(ORG, USER, 'codex')
      .catch((error: unknown) => error);
    expect((refused as Error).message).to.equal(ENGINE_NOT_INSTALLED_MESSAGE);
  });

  it('T-4: a broker older than R32-TRUST (no untrusted list) keeps today’s behaviour', async () => {
    const listed = await service({ unavailable: async () => ACP_REFS }).engines(ORG);
    expect(listed.map((e) => e.kind)).to.deep.equal(NATIVE);
  });

  it('T-5: the broker health client reads enginesUntrusted', async () => {
    const fetchImpl = (async () =>
      new Response(
        JSON.stringify({
          ok: true,
          modes: ['gvisor'],
          detail: { enginesUnavailable: 'acp-gemini@1,acp-qwen@2', enginesUntrusted: 'acp-gemini@1' },
        }),
        { status: 200 },
      )) as unknown as typeof fetch;
    const availability = new HttpEngineAvailability(
      async () => 'http://broker:8095',
      async () => 'credential',
      fetchImpl,
    );
    expect([...((await availability.untrusted()) ?? [])]).to.deep.equal(['acp-gemini@1']);
    expect([...((await availability.unavailable()) ?? [])]).to.deep.equal(['acp-gemini@1', 'acp-qwen@2']);
    const older = new HttpEngineAvailability(
      async () => 'http://broker:8095',
      async () => 'credential',
      (async () => new Response(JSON.stringify({ ok: true, detail: {} }), { status: 200 })) as unknown as typeof fetch,
    );
    expect(await older.untrusted()).to.equal(null);
  });
});
