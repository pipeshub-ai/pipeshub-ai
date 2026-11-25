import type {
  PolicyLayers,
  PolicyStorePurge,
} from '../../../../modules/control-plane/ports/policy-store';
import { pickHarnessStore } from '../../agent-harness/stores';
import { MongoPolicyStore } from './mongo';
import { PgPolicyStore } from './pg';

export const createPolicyStore = (): PolicyLayers & PolicyStorePurge =>
  pickHarnessStore<PolicyLayers & PolicyStorePurge>('control-plane/policy', {
    mongo: () => new MongoPolicyStore(),
    postgres: () => new PgPolicyStore(),
  });
