/**
 * Keeps every harness browser spec on the stack's scripted model (deployment/e2e/fake_llm). The harness lane makes
 * the scripted model the org's default; a stack people use keeps its own default, which may be a paid one.
 *
 * Two guards, so no spec has to remember either:
 * - `pinScriptedModel` rewrites the model of every chat turn a browser context sends to the one E2E_LLM_MODEL_KEY
 *   names. The shared fixture (`fixtures/base.fixture.ts`) applies it to every context on the harness lane; the
 *   server makes a turn's title and follow-ups with the turn's model, so those follow.
 * - `scriptedModelProblem` is checked before any harness spec runs (`setup/harness-model.guard.ts`): a stack whose
 *   default model is not the scripted one is refused unless E2E_LLM_MODEL_KEY names the scripted model there.
 *
 * A new default chat is also pinned to a coding engine that needs no person's own sign-in (WP65-SCRIPT): on a stack people
 * use the admin's own default may be Claude Code on their own Claude account, and a scripted turn handed to it waits for a
 * sign-in nobody gives. An agent's chat keeps the engines its profile lists; a spec on an agent whose only engine
 * signs in skips (as the human-test stack's smoke does).
 */
import type { BrowserContext, Page } from '@playwright/test';

/**
 * A chat turn's POST: a new conversation's, a follow-up message's, or an agent's, on the chat stream route or on the
 * durable admission route (`…/runs`, which a message sent to a running task also takes and which can start a run of
 * its own after it).
 */
export const TURN_STREAM =
  /^\/api\/v1\/(?:(?:agents\/[^/]+\/)?conversations\/(?:[^/]+\/messages\/)?stream|agents\/[^/]+\/(?:conversations\/[^/]+\/)?runs|conversations\/(?:[^/]+\/)?runs)$/;

export const DEFAULT_SCRIPTED_MODEL_NAME = 'e2e-llm';

/** A new default chat's create route: the only turn that takes a `codingEngine` (CMDUI2). */
export const NEW_DEFAULT_CHAT = /^\/api\/v1\/conversations\/stream$/;

/** Runs on the org's model gateway, so on the scripted model, and signs nobody in. */
export const DEFAULT_SCRIPTED_ENGINE = 'opencode';

/** The variables the guards read (`process.env` on a lane). */
export interface ScriptedModelEnv {
  E2E_LLM_MODEL_KEY?: string;
  E2E_LLM_MODEL_NAME?: string;
  /** The coding engine a new default chat is pinned to; `none` keeps every turn on the chat loop. */
  E2E_CODING_ENGINE?: string;
  [name: string]: string | undefined;
}

/** A model the stack offers, as `GET /api/v1/configurationManager/ai-models/available/llm` lists it. */
export interface OfferedModel {
  modelKey: string;
  modelName: string;
  provider?: string;
  isDefault?: boolean;
}

const scriptedName = (env: ScriptedModelEnv): string => env.E2E_LLM_MODEL_NAME || DEFAULT_SCRIPTED_MODEL_NAME;

/**
 * Why harness specs must not run against this stack, or null when every turn reaches the scripted model: the pinned
 * key must name the scripted model, and without a pin the stack's default must be it.
 */
export function scriptedModelProblem(models: readonly OfferedModel[], env: ScriptedModelEnv): string | null {
  const name = scriptedName(env);
  const key = env.E2E_LLM_MODEL_KEY;
  if (key) {
    const pinned = models.find((m) => m.modelKey === key);
    if (!pinned) return `E2E_LLM_MODEL_KEY=${key} is not a model this stack offers; set it to the scripted model's key.`;
    if (pinned.modelName !== name) {
      return `E2E_LLM_MODEL_KEY=${key} names "${pinned.modelName}", not the scripted model "${name}"; turns would reach it.`;
    }
    return null;
  }
  const fallback = models.find((m) => m.isDefault) ?? models[0];
  if (!fallback) return 'The stack offers no model, so no turn can reach the scripted model.';
  if (fallback.modelName === name) return null;
  return (
    `The stack's default model is "${fallback.modelName}", not the scripted model "${name}", and E2E_LLM_MODEL_KEY is ` +
    'unset: a turn would reach a real (possibly paid) model. Set E2E_LLM_MODEL_KEY to the scripted model\'s key ' +
    '(deployment/e2e/run.sh passes the seed\'s llmKey).'
  );
}

/**
 * A turn's body with the scripted model in place of whatever the page chose; on a new default chat (`path`), with
 * the scripted engine unless the page chose one itself (`/engine`, which specs of the engine picker use).
 */
export function pinnedTurnBody(
  body: Record<string, unknown>,
  env: ScriptedModelEnv,
  path?: string,
): Record<string, unknown> {
  const key = env.E2E_LLM_MODEL_KEY;
  if (!key) return body;
  const name = scriptedName(env);
  const pinned: Record<string, unknown> = { ...body, modelKey: key, modelName: name, modelFriendlyName: name };
  if (path !== undefined && NEW_DEFAULT_CHAT.test(path) && body.codingEngine === undefined) {
    pinned.codingEngine = env.E2E_CODING_ENGINE || DEFAULT_SCRIPTED_ENGINE;
  }
  return pinned;
}

const pinned = new WeakSet<Page | BrowserContext>();

/** Sends every chat turn of `target` (a context covers all its pages) to the scripted model E2E_LLM_MODEL_KEY names. */
export async function pinScriptedModel(target: Page | BrowserContext, env: ScriptedModelEnv = process.env): Promise<void> {
  if (!env.E2E_LLM_MODEL_KEY || pinned.has(target)) return;
  pinned.add(target);
  await target.route(
    (url) => TURN_STREAM.test(url.pathname),
    async (route) => {
      const request = route.request();
      if (request.method() !== 'POST') return route.fallback();
      const body = (request.postDataJSON() ?? {}) as Record<string, unknown>;
      const path = new URL(request.url()).pathname;
      return route.fallback({ postData: JSON.stringify(pinnedTurnBody(body, env, path)) });
    },
  );
}
