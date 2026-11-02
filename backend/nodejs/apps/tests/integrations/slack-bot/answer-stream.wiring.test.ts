import 'reflect-metadata';
import sinon from 'sinon';
import { resolve } from 'path';
import { ConfigService } from '../../../src/modules/tokens_manager/services/cm.service';
import { describeAnswerWiring } from './slack-bot-harness';

const LIBS = resolve(__dirname, '../../../src/libs');

describeAnswerWiring(
  'slack bot: one answer in the thread',
  resolve(__dirname, '../../../src/integrations/slack-bot/src'),
  () => {
    sinon.stub(ConfigService, 'getInstance').returns({
      getScopedJwtSecret: async () => 'scoped-secret',
    } as never);
  },
  {
    [`${LIBS}/services/redis/connectionProviderFactory.ts`]: {
      RedisConnectionProviderFactory: {
        ensureProviderModuleLoaded: async () => undefined,
        register: () => undefined,
      },
      getPreparedRedisProvider: async () => undefined,
    },
    [`${LIBS}/repositories/doc-store.ts`]: { initDocStore: async () => undefined },
  },
);
