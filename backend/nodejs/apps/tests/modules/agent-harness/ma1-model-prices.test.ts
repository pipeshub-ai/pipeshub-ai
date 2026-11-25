/**
 * MA1 (QA37-U9, Node half): Node's release copy of the price table equals the
 * shared vector `contracts/harness/vectors/model-prices.json`, which Python
 * holds to `MODEL_PRICES` (`test_ma1_model_prices.py`).
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { pricedModels, usdPerMTok } from '../../../src/modules/agent-harness/domain/model-prices-release';

const VECTOR = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/model-prices.json'), 'utf8'),
) as { usdPerMTok: Record<string, number> };

describe('MA1 the price of a model (QA37-U9)', () => {
  it('QA37-U9: the release copy holds exactly the vector’s rows', () => {
    expect(pricedModels()).to.deep.equal(VECTOR.usdPerMTok);
  });

  it('QA37-U9: each model is priced at the vector’s number', () => {
    expect(Object.keys(VECTOR.usdPerMTok)).to.have.length.greaterThan(0);
    for (const [model, price] of Object.entries(VECTOR.usdPerMTok)) {
      expect(usdPerMTok(model), model).to.equal(price);
    }
  });

  it('QA37-U9: a model without a row has no price (never zero), an object member’s name included', () => {
    expect(usdPerMTok('no-such-model')).to.equal(null);
    expect(usdPerMTok('constructor')).to.equal(null);
    expect(usdPerMTok('')).to.equal(null);
  });
});
