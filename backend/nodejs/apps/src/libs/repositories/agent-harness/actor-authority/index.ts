import type { ActorAuthority } from '../../../../modules/agent-harness/ports/schedules';
import { pickHarnessStore } from '../stores';
import { HarnessActorAuthority } from './mongo';
import { PgActorAuthority } from './pg';

export const createActorAuthority = (jwtSecret: () => string): ActorAuthority =>
  pickHarnessStore<ActorAuthority>('agent-harness/actor-authority', {
    mongo: () => new HarnessActorAuthority(jwtSecret),
    postgres: () => new PgActorAuthority(jwtSecret),
  });
