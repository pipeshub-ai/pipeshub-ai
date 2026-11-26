/**
 * QA6-F5: one definition of `chatMode` per kind of chat route. The assistant's
 * regenerate route took any non-empty string while its stream routes refused
 * `image`, so a retired mode reached admission through regenerate. Every chat
 * route's validator is read from the router's source here and held to a named,
 * closed definition; a stored conversation whose mode no route accepts any
 * more is served in the default mode.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import { PIPESHUB_CHAT_MODE } from '../../../src/modules/enterprise_search/constants/constants';
import * as validators from '../../../src/modules/enterprise_search/validators/es_validators';

const {
  AGENT_CHAT_MODES,
  assistantChatModeSchema,
  DEFAULT_ASSISTANT_CHAT_MODE,
  servedAssistantChatMode,
  UNIVERSAL_STREAM_CHAT_MODES,
  withServedAssistantMode,
} = validators;

const ROUTES = readFileSync(
  join(__dirname, '../../../src/modules/enterprise_search/routes/es.routes.ts'),
  'utf8',
);

/** Every `router.post(path, …, validate(schema), …)` of the chat router, as [path, schema name]. */
const validated = (): Array<[string, string]> =>
  [...ROUTES.matchAll(/router\.post\(\s*'([^']+)',([\s\S]*?)\n {2}\);/g)].flatMap((route) => {
    const schema = /ValidationMiddleware\.validate\((\w+)\)/.exec(route[2] as string);
    return schema === null ? [] : [[route[1] as string, schema[1] as string] as [string, string]];
  });

type Definition = 'assistant' | 'agent' | 'every-surface' | 'free text' | 'unknown';

/** Which definition a validator holds `body.chatMode` to; null when its body takes no mode. */
function definitionOf(schemaName: string): Definition | null {
  const schema = (validators as Record<string, unknown>)[schemaName] as z.ZodObject<{ body?: z.ZodTypeAny }> | undefined;
  const body = schema?.shape?.body as z.ZodObject<Record<string, z.ZodTypeAny>> | undefined;
  let mode = body?.shape?.['chatMode'];
  if (mode === undefined) return null;
  while (mode instanceof z.ZodOptional || mode instanceof z.ZodDefault) mode = mode._def.innerType as z.ZodTypeAny;
  if (mode === assistantChatModeSchema) return 'assistant';
  // zod 4 (main #4674) has one enum type: a native enum is a ZodEnum whose `enum` holds the object's own entries.
  if (mode instanceof z.ZodEnum && JSON.stringify(mode.options) === JSON.stringify(AGENT_CHAT_MODES)) return 'agent';
  if (mode instanceof z.ZodEnum && JSON.stringify(mode.enum) === JSON.stringify(PIPESHUB_CHAT_MODE)) return 'every-surface';
  return mode instanceof z.ZodString ? 'free text' : 'unknown';
}

const ID = 'a'.repeat(24);

describe('chat routes: one definition of chatMode (QA6-F5)', () => {
  const routes = validated().flatMap(([path, schema]) => {
    const definition = definitionOf(schema);
    return definition === null ? [] : [{ path, schema, definition }];
  });

  it('F5-1: every chat route that takes a mode is found, and none takes free text', () => {
    expect(routes.map((r) => r.path)).to.include.members([
      '/stream',
      '/:conversationId/messages/stream',
      '/:conversationId/message/:messageId/regenerate',
      '/:agentKey/conversations/stream',
      '/:agentKey/conversations/:conversationId/messages/stream',
      '/:agentKey/conversations/:conversationId/message/:messageId/regenerate',
    ]);
    for (const route of routes) expect(['assistant', 'agent', 'every-surface'], `${route.path} (${route.schema})`).to.include(route.definition);
  });

  it('F5-1: the assistant\'s public stream and regenerate routes share one schema, an agent\'s share theirs', () => {
    const by = (path: string) => routes.find((r) => r.path === path)?.definition;
    for (const path of ['/stream', '/:conversationId/messages/stream', '/:conversationId/message/:messageId/regenerate']) {
      expect(by(path), path).to.equal('assistant');
    }
    for (const path of [
      '/:agentKey/conversations/stream',
      '/:agentKey/conversations/:conversationId/messages/stream',
      '/:agentKey/conversations/:conversationId/message/:messageId/regenerate',
    ]) {
      expect(by(path), path).to.equal('agent');
    }
  });

  it('F5-2: a regenerate refuses what the stream routes refuse, and may leave the mode out', () => {
    const regenerate = (body: Record<string, unknown>) =>
      validators.regenerateAnswersParamsSchema.safeParse({ params: { conversationId: ID, messageId: ID }, body }).success;
    for (const chatMode of UNIVERSAL_STREAM_CHAT_MODES) expect(regenerate({ chatMode }), chatMode).to.equal(true);
    expect(regenerate({})).to.equal(true);
    for (const chatMode of ['image', 'agent:auto', 'quick', 'Internal_Search', '', 'anything']) {
      expect(regenerate({ chatMode }), chatMode).to.equal(false);
      expect(validators.enterpriseSearchStreamCreateSchema.safeParse({ body: { query: 'q', chatMode } }).success, chatMode).to.equal(false);
      expect(
        validators.addMessageStreamParamsSchema.safeParse({ params: { conversationId: ID }, body: { query: 'q', chatMode } }).success,
        chatMode,
      ).to.equal(false);
    }
  });

  it('F5-3: a stored mode no route accepts any more is served as the default; one a route accepts is served as stored', () => {
    expect(DEFAULT_ASSISTANT_CHAT_MODE).to.equal('internal_search');
    for (const kept of [...UNIVERSAL_STREAM_CHAT_MODES, ...Object.values(PIPESHUB_CHAT_MODE)]) {
      expect(servedAssistantChatMode(kept), kept).to.equal(kept);
    }
    expect(servedAssistantChatMode('agent:planExecute')).to.equal('agent');
    for (const retired of ['image', 'analysis', 'Image', 'agentic']) {
      expect(servedAssistantChatMode(retired), retired).to.equal(DEFAULT_ASSISTANT_CHAT_MODE);
    }
  });

  it('F5-3: a conversation is served with that mode and nothing else changed; one that stores no mode is untouched', () => {
    const stored = { title: 't', modelInfo: { modelKey: 'm1', chatMode: 'image' } };
    expect(withServedAssistantMode(stored)).to.deep.equal({ title: 't', modelInfo: { modelKey: 'm1', chatMode: 'internal_search' } });
    expect(stored.modelInfo.chatMode, 'the stored document is not rewritten').to.equal('image');
    for (const untouched of [null, undefined, { title: 't' }, { modelInfo: null }, { modelInfo: { modelKey: 'm1' } }, { modelInfo: { chatMode: '' } }]) {
      expect(withServedAssistantMode(untouched)).to.equal(untouched);
    }
    const valid = { modelInfo: { chatMode: 'web_search' } };
    expect(withServedAssistantMode(valid)).to.deep.equal(valid);
  });

  it('F5-3: the assistant\'s conversation read is the one place that serves it', () => {
    const controller = readFileSync(join(__dirname, '../../../src/modules/enterprise_search/controller/es_controller.ts'), 'utf8');
    expect(controller.match(/withServedAssistantMode\(/g)).to.have.length(1);
  });
});
