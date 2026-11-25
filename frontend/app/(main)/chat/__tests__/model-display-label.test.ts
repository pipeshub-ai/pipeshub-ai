/**
 * P2.2 DN-5: a message's model badge shows the configured display name. Older messages stored
 * the raw model name as their display name; the loaded catalog corrects it when it knows the model.
 */
import { describe, it, expect } from 'vitest';
import { modelDisplayLabel } from '../utils/apply-conversation-model-info';
import type { AvailableLlmModel, ModelInfo } from '../types';

const catalog = [
  { modelKey: 'k1', modelName: 'gpt-4o', provider: 'openAI', modelFriendlyName: 'Team GPT' },
  { modelKey: 'k2', modelName: 'claude-x', provider: 'anthropic' },
] as unknown as AvailableLlmModel[];

const info = (fields: Partial<ModelInfo>): ModelInfo => ({ chatMode: 'quick', ...fields }) as ModelInfo;

describe('DN-5 message model badge', () => {
  it('uses the catalog display name for a message stored without one', () => {
    expect(modelDisplayLabel(info({ modelKey: 'k1', modelName: 'gpt-4o', modelFriendlyName: 'gpt-4o' }), catalog)).toBe('Team GPT');
    expect(modelDisplayLabel(info({ modelKey: 'k1', modelName: 'gpt-4o' }), catalog)).toBe('Team GPT');
  });

  it('keeps what the message stored when the catalog has no display name or no such model', () => {
    expect(modelDisplayLabel(info({ modelKey: 'k2', modelName: 'claude-x', modelFriendlyName: 'Support Claude' }), catalog)).toBe('Support Claude');
    expect(modelDisplayLabel(info({ modelKey: 'gone', modelName: 'gpt-3' }), catalog)).toBe('gpt-3');
    expect(modelDisplayLabel(undefined, catalog)).toBe('');
  });
});
