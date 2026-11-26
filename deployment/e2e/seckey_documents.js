// R31-SECKEY: the two documents Node keeps encrypted with its own key (the org's AI models and web search settings),
// with the lane's canary entries added, written as the admin pages store them but without the pages' checks: a model's
// health check or a search provider's test would send each canary to that provider's real endpoint.
//
//   node /e2e/seckey_documents.js plant   < {"aiModels": {"llm": [...], "embedding": [...]}, "webSearch": [...]}
//   node /e2e/seckey_documents.js restore
//
// Runs in the app container with the stack's own Node build (/app/backend/dist), its configuration and SECRET_KEY. The
// entries come on stdin and each is tagged (``seckey-`` in its modelKey or providerKey); ``restore`` removes every
// tagged entry and nothing else. Embeddings planted go first: any embedding entry replaces the built-in model, and
// nothing the lane runs embeds while they are planted. A planted web search provider marked default takes the default
// from the org's own. Prints the counts, never a value.
'use strict';

const path = require('node:path');

const DIST = '/app/backend/dist';
const { KeyValueStoreService } = require(path.join(DIST, 'libs/services/keyValueStore.service'));
const { EncryptionService } = require(path.join(DIST, 'libs/encryptor/encryptor'));
const { loadConfigurationManagerConfig } = require(path.join(DIST, 'modules/configuration_manager/config/config'));
const { configPaths } = require(path.join(DIST, 'modules/configuration_manager/paths/paths'));

const TAG = 'seckey-';
const tagged = (entry) => [entry?.modelKey, entry?.providerKey].some((k) => typeof k === 'string' && k.startsWith(TAG));

async function stdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {};
}

function merge(own, added, first) {
  const kept = (Array.isArray(own) ? own : []).filter((entry) => !tagged(entry));
  if (added.some((entry) => !tagged(entry))) throw new Error('an entry to plant is not tagged');
  return first ? [...added, ...kept] : [...kept, ...added];
}

async function main() {
  const action = process.argv[2];
  if (action !== 'plant' && action !== 'restore') throw new Error('usage: seckey_documents.js plant|restore');
  const body = action === 'plant' ? await stdin() : {};
  const config = loadConfigurationManagerConfig();
  const store = KeyValueStoreService.getInstance(config);
  await store.connect();
  const crypto = EncryptionService.getInstance(config.algorithm, config.secretKey);
  const read = async (key) => {
    const stored = await store.get(key);
    return stored ? JSON.parse(crypto.decrypt(stored)) : {};
  };
  const write = (key, value) => store.set(key, crypto.encrypt(JSON.stringify(value)));

  const models = await read(configPaths.aiModels);
  models.llm = merge(models.llm, body.aiModels?.llm ?? [], false);
  models.embedding = merge(models.embedding, body.aiModels?.embedding ?? [], true);
  await write(configPaths.aiModels, models);

  const search = await read(configPaths.webSearch);
  const providers = body.webSearch ?? [];
  const own = (Array.isArray(search.providers) ? search.providers : []).filter((entry) => !tagged(entry));
  if (providers.some((entry) => entry.isDefault)) for (const entry of own) entry.isDefault = false;
  search.providers = merge(own, providers, false);
  await write(configPaths.webSearch, search);

  process.stdout.write(`${JSON.stringify({ documents: action, llm: models.llm.length, embedding: models.embedding.length,
    webSearch: search.providers.length })}\n`);
}

main().then(
  () => process.exit(0),
  (error) => {
    process.stderr.write(`seckey_documents: ${error.message}\n`);
    process.exit(1);
  },
);
