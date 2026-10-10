import mongoose, { ClientSession } from 'mongoose';

export interface IMutationRunner {
  /**
   * On a replica set `work` runs inside one transaction and may run again after a transient
   * error, so it must hold no side effects outside `session`. Standalone: `session` is
   * undefined and the steps run once, in order; each is idempotent so a client retry converges (74 section 9).
   */
  run<T>(work: (session: ClientSession | undefined) => Promise<T>): Promise<T>;
}

export class MongoMutationRunner implements IMutationRunner {
  constructor(private readonly rsAvailable: boolean) {}

  async run<T>(
    work: (session: ClientSession | undefined) => Promise<T>,
  ): Promise<T> {
    if (!this.rsAvailable) {
      return work(undefined);
    }
    const session = await mongoose.startSession();
    try {
      return await session.withTransaction(() => work(session));
    } finally {
      await session.endSession();
    }
  }
}
