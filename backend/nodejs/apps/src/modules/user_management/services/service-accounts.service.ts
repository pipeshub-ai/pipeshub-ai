import { injectable, inject } from 'inversify';
import mongoose from 'mongoose';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  ConflictError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { Users, User } from '../schema/users.schema';
import { UserGroups } from '../schema/userGroup.schema';
import {
  EntitiesEventProducer,
  Event,
  EventType,
  SyncAction,
  UserAddedEvent,
  UserDeletedEvent,
  UserUpdatedEvent,
} from './entity_events.service';
import {
  SERVICE_ACCOUNT_ROLE,
  buildServiceAccountEmail,
  isValidServiceAccountSlug,
  serviceAccountSlugFromEmail,
  SERVICE_ACCOUNT_SLUG_MIN_LENGTH,
  SERVICE_ACCOUNT_SLUG_MAX_LENGTH,
} from '../constants/service-account.constants';

export interface ServiceAccountView {
  id: string;
  slug: string;
  fullName: string;
  email: string;
  description?: string;
  isDisabled: boolean;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface CreateServiceAccountInput {
  slug: string;
  fullName: string;
  description?: string;
}

/**
 * The part of the token service this one needs.
 *
 * Service accounts and the tokens they hold are owned by different modules,
 * with their own dependency containers, so the connection is made explicitly
 * at wiring time rather than by reaching across. Stated as the one operation
 * that is needed, so what this service can do to tokens is visible here.
 */
export interface ServiceAccountTokenRevoker {
  revokeAllForServiceAccount(
    orgId: string,
    serviceAccountId: string,
  ): Promise<void>;
}

export interface UpdateServiceAccountInput {
  fullName?: string;
  description?: string;
  isDisabled?: boolean;
}

/**
 * Mongoose types `_id` loosely enough that stringifying it directly trips the
 * lint rule guarding against `[object Object]` creeping into output. Going
 * through ObjectId keeps that guarantee visible in one place.
 */
/** Mongo's unique-index violation, whatever driver wrapper it arrives in. */
function isDuplicateKeyError(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as { code?: unknown }).code === 11000
  );
}

function idOf(doc: { _id?: unknown }): string {
  return (doc._id as mongoose.Types.ObjectId).toString();
}

function toView(
  user: User & { createdAt?: Date; updatedAt?: Date },
  orgId: string,
): ServiceAccountView {
  return {
    id: idOf(user),
    slug: serviceAccountSlugFromEmail(user.email, orgId),
    fullName: user.fullName ?? '',
    email: user.email,
    description: user.description,
    isDisabled: user.isDisabled ?? false,
    createdAt: user.createdAt,
    updatedAt: user.updatedAt,
  };
}

/**
 * Service accounts: machine identities that automation authenticates as.
 *
 * The important design decision is that a service account is an ordinary user
 * record with `kind: 'service'`, rather than a new sort of principal. That is
 * what lets it reach the permission graph through the path human users already
 * take, and it is why "what can this service account see" is answered by the
 * same code that answers the question for people. A parallel notion of access
 * would be a second thing to keep correct, and the two would drift.
 */
@injectable()
export class ServiceAccountsService {
  constructor(
    @inject('Logger') private readonly logger: Logger,
    @inject('EntitiesEventProducer')
    private readonly eventService: EntitiesEventProducer,
  ) {}

  private tokenRevoker?: ServiceAccountTokenRevoker;

  /**
   * Supplied once both containers exist. Until it is, deleting a service
   * account still stops its tokens, because the auth middleware refuses a
   * deleted account — revocation is what makes that survive a restore.
   */
  setTokenRevoker(revoker: ServiceAccountTokenRevoker): void {
    this.tokenRevoker = revoker;
  }

  async create(
    orgId: string,
    input: CreateServiceAccountInput,
  ): Promise<ServiceAccountView> {
    const slug = input.slug.trim().toLowerCase();
    if (!isValidServiceAccountSlug(slug)) {
      throw new BadRequestError(
        `Name must be ${String(SERVICE_ACCOUNT_SLUG_MIN_LENGTH)}-${String(SERVICE_ACCOUNT_SLUG_MAX_LENGTH)} characters of lowercase letters, digits and single hyphens, and may not start or end with a hyphen`,
      );
    }

    const email = buildServiceAccountEmail(slug, orgId);

    // Deliberately not filtered by isDeleted. Deleting a service account only
    // marks the row, and email stays uniquely indexed across the whole
    // collection, so a lookup that skipped deleted rows would report the name
    // as free and then fail on the index — a 500 for what is really "this
    // name is in use" or "this name is being reused".
    const existing = await Users.findOne({ email }).exec();

    if (existing) {
      // Only this organisation's own deleted service account may be brought
      // back. Email uniqueness is global, so the row holding this address
      // need not belong here: another organisation could have invited a
      // person at it and then deleted them. Restoring that row would undelete
      // someone else's tenant's user and announce it to the graph under this
      // organisation's id. Anything that is not ours, not a service account,
      // or not deleted is simply a name in use.
      const isOurDeletedServiceAccount =
        existing.isDeleted === true &&
        existing.kind === 'service' &&
        existing.orgId.toString() === orgId;

      if (!isOurDeletedServiceAccount) {
        throw new ConflictError(
          `A service account named "${slug}" already exists`,
        );
      }
      return await this.restore(existing, orgId, slug, input);
    }

    const serviceAccount = new Users({
      orgId: new mongoose.Types.ObjectId(orgId),
      email,
      fullName: input.fullName.trim(),
      description: input.description?.trim(),
      kind: 'service',
      // Never an admin, whoever creates it. A service account that could hold
      // admin rights would be a way to obtain them without a person attached.
      role: SERVICE_ACCOUNT_ROLE,
      isDisabled: false,
    });

    // Saved before the event goes out: the consumer builds a permission-graph
    // node from it, and a node for a record that failed to save would be a
    // principal with access and no way to administer it.
    try {
      await serviceAccount.save();
    } catch (error) {
      // Two administrators creating the same name at once both pass the check
      // above; the unique index fails the loser. That is a conflict, not a
      // server error.
      if (isDuplicateKeyError(error)) {
        throw new ConflictError(
          `A service account named "${slug}" already exists`,
        );
      }
      throw error;
    }

    await UserGroups.updateOne(
      { orgId: serviceAccount.orgId, type: 'everyone' },
      { $addToSet: { users: serviceAccount._id } },
    );

    const addedPayload: UserAddedEvent = {
      orgId,
      userId: idOf(serviceAccount),
      fullName: serviceAccount.fullName,
      email: serviceAccount.email,
      syncAction: SyncAction.Immediate,
    };
    await this.publish({
      eventType: EventType.NewUserEvent,
      timestamp: Date.now(),
      payload: addedPayload,
    });

    this.logger.info('Service account created', {
      orgId,
      serviceAccountId: idOf(serviceAccount),
      slug,
    });

    return toView(serviceAccount, orgId);
  }

  /**
   * Brings back a previously deleted service account under the same name.
   *
   * The row is reused rather than a new one created, because the address is
   * uniquely indexed and the graph node is keyed by it: publishing userAdded
   * for this address makes the consumer reactivate the node it already has.
   * Creating a second row would leave the first holding that address forever
   * and the name permanently unusable.
   *
   * Every field is reset from the new request, so nothing of the old account
   * survives except its identity. It comes back enabled and not deleted.
   */
  private async restore(
    existing: User,
    orgId: string,
    slug: string,
    input: CreateServiceAccountInput,
  ): Promise<ServiceAccountView> {
    // One conditional update rather than read-then-save, because two
    // administrators can reach here with the same deleted document in hand.
    // Mongoose's version key does not guard scalar assignments, so both saves
    // would succeed, both would publish userAdded, and the later would
    // overwrite the earlier one's details. Making `isDeleted: true` part of
    // the query means the transition happens once: the first request restores
    // the record, and the second matches nothing.
    const restored = await Users.findOneAndUpdate(
      // Narrowed the same way the check above is, so the update cannot land
      // on another tenant's row even if the record changed underneath us.
      {
        _id: existing._id,
        orgId,
        kind: 'service',
        isDeleted: true,
      },
      {
        $set: {
          fullName: input.fullName.trim(),
          kind: 'service',
          role: SERVICE_ACCOUNT_ROLE,
          isDisabled: false,
          isDeleted: false,
          // A description the caller did give.
          ...(input.description === undefined
            ? {}
            : { description: input.description.trim() }),
        },
        $unset: {
          deletedBy: '',
          // One they did not. Setting a field to undefined is a no-op in
          // Mongoose, so writing `description: undefined` would quietly leave
          // the deleted account's old text in place — which contradicts what
          // this method promises, that nothing of the old account survives
          // but its identity.
          ...(input.description === undefined ? { description: '' } : {}),
        },
      },
      { new: true },
    ).exec();

    if (!restored) {
      throw new ConflictError(
        `A service account named "${slug}" already exists`,
      );
    }

    // Before the identity is handed back. Restoring reuses the original
    // record, so a token minted before the deletion would otherwise start
    // working again for whoever reused the name.
    await this.tokenRevoker?.revokeAllForServiceAccount(orgId, idOf(restored));

    await UserGroups.updateOne(
      { orgId: restored.orgId, type: 'everyone' },
      { $addToSet: { users: restored._id } },
    );

    const addedPayload: UserAddedEvent = {
      orgId,
      userId: idOf(restored),
      fullName: restored.fullName,
      email: restored.email,
      syncAction: SyncAction.Immediate,
    };
    await this.publish({
      eventType: EventType.NewUserEvent,
      timestamp: Date.now(),
      payload: addedPayload,
    });

    this.logger.info('Service account restored', {
      orgId,
      serviceAccountId: idOf(restored),
      slug,
    });

    return toView(restored, orgId);
  }

  async list(orgId: string): Promise<ServiceAccountView[]> {
    const accounts = await Users.find({
      orgId,
      kind: 'service',
      isDeleted: false,
    })
      .sort({ createdAt: -1 })
      .exec();
    return accounts.map((account) => toView(account, orgId));
  }

  async get(orgId: string, id: string): Promise<ServiceAccountView> {
    return toView(await this.findOrThrow(orgId, id), orgId);
  }

  async update(
    orgId: string,
    id: string,
    input: UpdateServiceAccountInput,
  ): Promise<ServiceAccountView> {
    const account = await this.findOrThrow(orgId, id);

    if (input.fullName !== undefined) account.fullName = input.fullName.trim();
    if (input.description !== undefined) {
      account.description = input.description.trim();
    }
    if (input.isDisabled !== undefined) account.isDisabled = input.isDisabled;

    await account.save();

    // The graph keeps its own copy of the display name, so a rename has to
    // reach it too or search results will go on showing the old one.
    if (input.fullName !== undefined) {
      const updatedPayload: UserUpdatedEvent = {
        orgId,
        userId: idOf(account),
        fullName: account.fullName,
        email: account.email,
      };
      await this.publish({
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: updatedPayload,
      });
    }

    this.logger.info('Service account updated', {
      orgId,
      serviceAccountId: id,
      disabled: account.isDisabled,
    });

    return toView(account, orgId);
  }

  async remove(orgId: string, id: string): Promise<void> {
    const account = await this.findOrThrow(orgId, id);

    account.isDeleted = true;
    await account.save();

    await this.tokenRevoker?.revokeAllForServiceAccount(orgId, idOf(account));

    await UserGroups.updateMany(
      { orgId: account.orgId },
      { $pull: { users: account._id } },
    );

    const deletedPayload: UserDeletedEvent = {
      orgId,
      userId: idOf(account),
      email: account.email,
    };
    await this.publish({
      eventType: EventType.DeleteUserEvent,
      timestamp: Date.now(),
      payload: deletedPayload,
    });

    this.logger.info('Service account deleted', {
      orgId,
      serviceAccountId: id,
    });
  }

  /**
   * Looks a service account up within one organisation.
   *
   * `kind: 'service'` is part of the query rather than something checked
   * afterwards, so this can never be used to reach a human user's record by
   * passing their id to a service-account route.
   */
  private async findOrThrow(orgId: string, id: string): Promise<User> {
    if (!mongoose.Types.ObjectId.isValid(id)) {
      throw new NotFoundError('Service account not found');
    }
    const account = await Users.findOne({
      _id: id,
      orgId,
      kind: 'service',
      isDeleted: false,
    }).exec();
    if (!account) {
      throw new NotFoundError('Service account not found');
    }
    return account;
  }

  private async publish(event: Event): Promise<void> {
    // `start()` only connects if the producer is not already connected, so it
    // is safe to call. There is deliberately no matching `stop()`: the message
    // producer is a single instance shared with the notification producer, and
    // disconnecting it after each event could break a publication already in
    // flight elsewhere. Its lifecycle belongs to the container that made it.
    await this.eventService.start();
    await this.eventService.publishEvent(event);
  }
}
