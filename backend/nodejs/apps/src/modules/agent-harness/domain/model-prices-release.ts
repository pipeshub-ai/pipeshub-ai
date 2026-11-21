// Node's release copy of contracts/harness/vectors/model-prices.json (the projection of Python's
// `MODEL_PRICES`, short-context tier): the Node image ships compiled code only. A drift test holds
// the two equal; change the price table in Python, regenerate the vector, then copy its rows here.
const USD_PER_MTOK: Readonly<Record<string, number>> = Object.freeze({
  'claude-haiku-4-5': 6.0,
  'claude-haiku-4-5-20251001': 6.0,
  'claude-opus-4-8': 30.0,
  'claude-opus-5-5': 24.0,
  'claude-sonnet-4-6': 18.0,
  'claude-sonnet-5': 12.0,
  'claude-sonnet-5-5': 12.0,
  'gpt-4.1': 10.0,
  'gpt-4.1-mini': 2.0,
  'gpt-4.1-nano': 0.5,
  'gpt-4o': 12.5,
  'gpt-4o-mini': 0.75,
  'gpt-5': 11.25,
  'gpt-5-mini': 2.25,
  'gpt-5-nano': 0.45,
  'gpt-5-pro': 135.0,
  'gpt-5.1': 11.25,
  'gpt-5.2': 15.75,
  'gpt-5.2-pro': 189.0,
  'gpt-5.3-codex': 15.75,
  'gpt-5.4': 17.5,
  'gpt-5.4-mini': 5.25,
  'gpt-5.4-nano': 1.45,
  'gpt-5.4-pro': 210.0,
  'gpt-5.5': 35.0,
  'gpt-5.5-pro': 210.0,
  'gpt-5.6-luna': 1.4,
  'gpt-5.6-sol': 24.0,
  'gpt-5.6-terra': 14.0,
  'gpt-6-astra': 60.0,
  'gpt-6-luna': 0.6,
  'gpt-6-sol': 12.0,
  'gpt-6.1-sol': 12.0,
  o1: 75.0,
  o3: 10.0,
  'o3-mini': 5.5,
  'o3-pro': 100.0,
  'o4-mini': 5.5,
});

/** Input plus output list price per 1M tokens; null: the release prices no such model. */
export const usdPerMTok = (model: string): number | null =>
  Object.prototype.hasOwnProperty.call(USD_PER_MTOK, model)
    ? (USD_PER_MTOK[model] as number)
    : null;

/** Every priced model, for the drift test. */
export const pricedModels = (): Readonly<Record<string, number>> =>
  USD_PER_MTOK;
