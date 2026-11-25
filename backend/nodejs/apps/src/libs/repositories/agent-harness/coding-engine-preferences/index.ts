import type { CodingEnginePreferences } from '../../../../modules/agent-harness/ports/coding-engines';
import type { PersonalMemoryChoice } from '../../../../modules/agent-harness/ports/memory-store';
import { pickHarnessStore } from '../stores';
import { MongoCodingEnginePreferences } from './mongo';
import { PgCodingEnginePreferences } from './pg';

export const createCodingEnginePreferences = (): CodingEnginePreferences &
  PersonalMemoryChoice =>
  pickHarnessStore<CodingEnginePreferences & PersonalMemoryChoice>(
    'agent-harness/coding-engine-preferences',
    {
      mongo: () => new MongoCodingEnginePreferences(),
      postgres: () => new PgCodingEnginePreferences(),
    },
  );
