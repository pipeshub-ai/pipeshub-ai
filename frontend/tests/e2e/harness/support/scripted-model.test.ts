/**
 * The harness lane's model guard: no harness spec may start where a chat turn could reach a model other than the
 * scripted one (a stack people use may default to a paid model), and every turn a page sends on the lane is pinned to it.
 */
import { describe, expect, it, vi } from 'vitest';
import {
  DEFAULT_SCRIPTED_ENGINE,
  pinnedTurnBody,
  pinScriptedModel,
  scriptedModelProblem,
  TURN_STREAM,
  type OfferedModel,
} from './scripted-model';

const SCRIPTED: OfferedModel = { modelKey: 'k-e2e', modelName: 'e2e-llm', provider: 'openAICompatible', isDefault: false };
const PAID: OfferedModel = { modelKey: 'k-paid', modelName: 'gpt-6-luna', provider: 'azureOpenAI', isDefault: true };

describe('the harness lane refuses a stack whose turns could reach a paid model', () => {
  it('refuses a stack whose default is not the scripted model when no key is pinned', () => {
    const problem = scriptedModelProblem([PAID, SCRIPTED], {});
    expect(problem).toContain('"gpt-6-luna"');
    expect(problem).toContain('E2E_LLM_MODEL_KEY is unset');
  });

  it('starts on the lane, whose default is the scripted model', () => {
    expect(scriptedModelProblem([{ ...SCRIPTED, isDefault: true }, { ...PAID, isDefault: false }], {})).toBeNull();
  });

  it('starts on a stack people use when the pinned key names the scripted model', () => {
    expect(scriptedModelProblem([PAID, SCRIPTED], { E2E_LLM_MODEL_KEY: 'k-e2e' })).toBeNull();
  });

  it('refuses a pin to a paid model, or to a key the stack does not offer', () => {
    expect(scriptedModelProblem([PAID, SCRIPTED], { E2E_LLM_MODEL_KEY: 'k-paid' })).toContain('not the scripted model');
    expect(scriptedModelProblem([PAID, SCRIPTED], { E2E_LLM_MODEL_KEY: 'k-gone' })).toContain('not a model this stack offers');
  });

  it('refuses a stack that offers no model', () => {
    expect(scriptedModelProblem([], {})).toContain('offers no model');
  });

  it('honours a scripted model of another name', () => {
    const named = { ...SCRIPTED, modelName: 'fake-llm', isDefault: true };
    expect(scriptedModelProblem([named], { E2E_LLM_MODEL_NAME: 'fake-llm' })).toBeNull();
    expect(scriptedModelProblem([named], {})).toContain('"fake-llm"');
  });
});

describe('every turn a page sends is pinned to the scripted model', () => {
  it('matches each turn route, and nothing else', () => {
    for (const path of [
      '/api/v1/conversations/stream',
      '/api/v1/conversations/c1/messages/stream',
      '/api/v1/agents/a1/conversations/stream',
      '/api/v1/agents/a1/conversations/c1/messages/stream',
      // Durable admission: a new session, the session it continues, and a message sent to a running task.
      '/api/v1/conversations/runs',
      '/api/v1/conversations/c1/runs',
      '/api/v1/agents/a1/runs',
      '/api/v1/agents/a1/conversations/c1/runs',
    ]) {
      expect(TURN_STREAM.test(path), path).toBe(true);
    }
    for (const path of [
      '/api/v1/conversations/c1/runs/r1/stream',
      '/api/v1/conversations/c1/runs/r1/cancel',
      '/api/v1/agents/a1/conversations/c1/runs/r1/interactions/i1/decision',
      '/api/v1/agent-harness/schedules',
    ]) {
      expect(TURN_STREAM.test(path), path).toBe(false);
    }
  });

  it('replaces the model the page chose, and leaves the rest of the turn', () => {
    const body = { query: 'hi', modelKey: 'k-paid', modelName: 'gpt-6-luna', chatMode: 'quick' };
    expect(pinnedTurnBody(body, { E2E_LLM_MODEL_KEY: 'k-e2e' })).toEqual({
      query: 'hi',
      chatMode: 'quick',
      modelKey: 'k-e2e',
      modelName: 'e2e-llm',
      modelFriendlyName: 'e2e-llm',
    });
    expect(pinnedTurnBody(body, {})).toBe(body);
  });

  it('routes a whole context once, and rewrites a POST turn on its way out', async () => {
    const route = vi.fn();
    const context = { route } as unknown as Parameters<typeof pinScriptedModel>[0];
    await pinScriptedModel(context, { E2E_LLM_MODEL_KEY: 'k-e2e' });
    await pinScriptedModel(context, { E2E_LLM_MODEL_KEY: 'k-e2e' });
    expect(route).toHaveBeenCalledTimes(1);
    const [matches, handler] = route.mock.calls[0] as [(url: URL) => boolean, (r: unknown) => Promise<void>];
    expect(matches(new URL('http://x/api/v1/conversations/stream'))).toBe(true);
    const fallback = vi.fn();
    await handler({
      request: () => ({
        method: () => 'POST',
        url: () => 'http://x/api/v1/conversations/stream',
        postDataJSON: () => ({ query: 'q', modelKey: 'k-paid' }),
      }),
      fallback,
    });
    expect(JSON.parse(fallback.mock.calls[0][0].postData)).toMatchObject({
      query: 'q',
      modelKey: 'k-e2e',
      codingEngine: DEFAULT_SCRIPTED_ENGINE,
    });
  });
});

describe('WP65-SCRIPT a new default chat is pinned to an engine that signs nobody in', () => {
  const env = { E2E_LLM_MODEL_KEY: 'k-e2e' };

  it('WP65-SCRIPT-1 pins the scripted engine on a new default chat, and the model with it', () => {
    expect(pinnedTurnBody({ query: 'q' }, env, '/api/v1/conversations/stream')).toMatchObject({
      modelKey: 'k-e2e',
      codingEngine: 'opencode',
    });
    expect(pinnedTurnBody({ query: 'q' }, { ...env, E2E_CODING_ENGINE: 'none' }, '/api/v1/conversations/stream'))
      .toMatchObject({ codingEngine: 'none' });
  });

  it('WP65-SCRIPT-2 keeps an engine the page chose, and adds none to a follow-up or an agent chat', () => {
    expect(pinnedTurnBody({ codingEngine: 'codex' }, env, '/api/v1/conversations/stream').codingEngine).toBe('codex');
    for (const path of [
      '/api/v1/conversations/c1/messages/stream',
      '/api/v1/agents/a1/conversations/stream',
      '/api/v1/conversations/runs',
    ]) {
      expect(pinnedTurnBody({ query: 'q' }, env, path), path).not.toHaveProperty('codingEngine');
    }
  });

  it('WP65-SCRIPT-3 pins nothing off the lane', () => {
    expect(pinnedTurnBody({ query: 'q' }, {}, '/api/v1/conversations/stream')).toEqual({ query: 'q' });
  });
});
