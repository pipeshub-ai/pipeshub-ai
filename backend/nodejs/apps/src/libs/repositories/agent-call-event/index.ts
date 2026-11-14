import { MongoAgentCallRepository } from './mongo';
import { PgAgentCallRepository } from './pg';
import type { AgentCallRepository } from './types';
import { pickStore } from '../store-select';

export * from './types';

let repo: AgentCallRepository | undefined;

export const getAgentCallRepository = (): AgentCallRepository =>
  (repo ??= pickStore<AgentCallRepository>('agent-call-event', {
    postgres: () => new PgAgentCallRepository(),
    mongo: () => new MongoAgentCallRepository(),
  }));

/** Replaces the repository (tests); no argument restores the default. */
export const setAgentCallRepository = (r?: AgentCallRepository): void => {
  repo = r;
};
