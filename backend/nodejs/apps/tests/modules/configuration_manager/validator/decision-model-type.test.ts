/// <reference types="mocha" />
import { expect } from 'chai';

import { AI_MODEL_TYPES, isAIModelType } from '../../../../src/modules/configuration_manager/constants/constants';
import { toOpenAIModelList } from '../../../../src/modules/configuration_manager/services/openaiModelList';
import { AIModelsConfig } from '../../../../src/modules/configuration_manager/types/ai-models.types';
import {
  addProviderRequestSchema,
  aiModelsConfigSchema,
  modelType,
} from '../../../../src/modules/configuration_manager/validator/validators';

const typesafeEntry = {
  provider: 'typesafe',
  configuration: { model: 'jev-latest', apiKey: 'ts-secret' },
  isDefault: true,
};

describe('decision model type', () => {
  it('is a model type everywhere the type list is used', () => {
    expect(AI_MODEL_TYPES).to.include('decision');
    expect(isAIModelType('decision')).to.equal(true);
    expect(modelType.safeParse('decision').success).to.equal(true);
  });

  it('can be added like any other model', () => {
    const parsed = addProviderRequestSchema.safeParse({
      body: { modelType: 'decision', provider: 'typesafe', configuration: { model: 'jev-latest', apiKey: 'k' } },
    });
    expect(parsed.success).to.equal(true);
  });

  it('is accepted by the bulk aiModels config', () => {
    const parsed = aiModelsConfigSchema.safeParse({ body: { decision: [typesafeEntry] } });
    expect(parsed.success).to.equal(true);
  });

  it('is listed with the decision task and without secrets', () => {
    const listed = toOpenAIModelList({ decision: [typesafeEntry] } as unknown as AIModelsConfig);
    expect(listed.data).to.have.length(1);
    expect(listed.data[0]).to.include({ id: 'jev-latest', task: 'decision', owned_by: 'typesafe' });
    expect(JSON.stringify(listed)).to.not.include('ts-secret');
  });
});
