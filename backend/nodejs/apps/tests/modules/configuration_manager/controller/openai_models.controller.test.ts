/// <reference types="mocha" />
import { expect } from 'chai';
import sinon from 'sinon';

import { retrieveOpenAIModel } from '../../../../src/modules/configuration_manager/controller/openai_models.controller';
import { AIModelsConfig } from '../../../../src/modules/configuration_manager/types/ai-models.types';

function jsonResponse() {
  const res = {
    statusCode: 200,
    body: undefined as unknown,
    status(code: number) {
      this.statusCode = code;
      return this;
    },
    json(body: unknown) {
      this.body = body;
      return this;
    },
  };
  return res;
}

describe('retrieveOpenAIModel', () => {
  it('returns a model id that contains a percent sign', async () => {
    const stored = {
      llm: [{ provider: 'openAI', configuration: { model: 'my%model' } }],
    } as unknown as AIModelsConfig;
    const res = jsonResponse();
    const next = sinon.stub();

    await retrieveOpenAIModel(async () => stored)(
      { params: { 0: 'my%model' } } as any,
      res as any,
      next,
    );

    expect(next.called).to.equal(false);
    expect(res.statusCode).to.equal(200);
    expect((res.body as { id?: string }).id).to.equal('my%model');
  });
});
