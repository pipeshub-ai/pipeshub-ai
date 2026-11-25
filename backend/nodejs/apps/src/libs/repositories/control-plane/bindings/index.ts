import type { BindingStore } from '../../../../modules/control-plane/ports/binding-store';
import { pickHarnessStore } from '../../agent-harness/stores';
import { MongoBindingStore } from './mongo';
import { PgBindingStore } from './pg';

export const createBindingStore = (): BindingStore =>
  pickHarnessStore<BindingStore>('control-plane/bindings', {
    mongo: () => new MongoBindingStore(),
    postgres: () => new PgBindingStore(),
  });
