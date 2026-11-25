import { HttpEngineAvailability } from '../adapters/http/http-engine-availability';
import { EngineSignInService } from '../application/engine-signin-service';
import { CodingEngineService } from '../application/coding-engine-service';
import { HarnessAuthority } from '../application/harness-authority';
import { silentHarnessLogger } from '../application/harness-log';
import type { EngineProfileCeilings } from '../ports/engine-profiles';
import type { OrgMemberLookup } from '../ports/org-members';
import type {
  ModelAccessPolicies,
  ModelPlacementSource,
} from '../ports/model-access';
import type { OrgSignInSwitch } from '../ports/engine-signins';
import {
  ModelAccessService,
  offeringFromEnv,
  type PersonalModelKeys,
} from '../application/model-access-service';
import { groupAccessOf } from './group-access';
import { boundOrNull, HarnessBinder } from './context';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_CODING_ENGINES,
  AGENT_HARNESS_ENGINE_PROFILES,
  AGENT_HARNESS_ENGINE_SIGNINS,
  AGENT_HARNESS_ORG_MEMBERS,
  AGENT_HARNESS_MODEL_ACCESS,
  AGENT_HARNESS_MODEL_ACCESS_POLICIES,
  AGENT_HARNESS_ORG_SIGNIN_SWITCH,
  AGENT_HARNESS_PERSONAL_MODEL_KEYS,
  AGENT_HARNESS_MODEL_PLACEMENT,
  AGENT_HARNESS_WORKER_FEATURES,
} from './tokens';
import type { WorkerFeatureAvailability } from '../application/worker-feature-availability';
import { VENDOR_SIGNIN_FEATURE } from '../domain/worker-compat';

export const bindModelAccess: HarnessBinder = ({ container, dependencies }) => {
  const access = dependencies.modelAccess;
  if (access === undefined || container.isBound(AGENT_HARNESS_MODEL_ACCESS)) {
    return;
  }
  const policies = boundOrNull<ModelAccessPolicies>(
    container,
    AGENT_HARNESS_MODEL_ACCESS_POLICIES,
  );
  container
    .bind<ModelAccessService>(AGENT_HARNESS_MODEL_ACCESS)
    .toConstantValue(
      new ModelAccessService({
        offering: access.offering ?? offeringFromEnv(),
        // An extension's narrowing, bound before the built-in binders; unbound: nothing narrowed.
        ...(policies !== null ? { policies } : {}),
        models: access.models,
        // Read at each use, so a source bound after this binder is the one asked.
        placement: () =>
          boundOrNull<ModelPlacementSource>(
            container,
            AGENT_HARNESS_MODEL_PLACEMENT,
          ),
        ...(dependencies.modelAccounts !== undefined
          ? { accounts: dependencies.modelAccounts }
          : {}),
        // An extension's, bound before the built-in binders; unbound: nobody has a key of their own.
        personalKeys: () =>
          boundOrNull<PersonalModelKeys>(
            container,
            AGENT_HARNESS_PERSONAL_MODEL_KEYS,
          ),
        signIns: () =>
          boundOrNull<EngineSignInService>(
            container,
            AGENT_HARNESS_ENGINE_SIGNINS,
          ),
        // SIGNGAP: read at each use, so the sightings bound later by the execution binder are the ones asked.
        signInsCarried: () => {
          const features = boundOrNull<WorkerFeatureAvailability>(
            container,
            AGENT_HARNESS_WORKER_FEATURES,
          );
          return features === null
            ? Promise.resolve(true)
            : features.live(VENDOR_SIGNIN_FEATURE);
        },
      }),
    );
};

/** QA-26: who belongs to an org, for routes that name another user; bound whether or not SIGNIN is. */
export const bindOrgMembers: HarnessBinder = ({ container, dependencies }) => {
  const members = dependencies.orgMembers;
  if (members === undefined || container.isBound(AGENT_HARNESS_ORG_MEMBERS))
    return;
  container
    .bind<OrgMemberLookup>(AGENT_HARNESS_ORG_MEMBERS)
    .toConstantValue(members);
};

/** SIGNIN: bound where the user and org records are; model access decides who may use a sign-in. */
export const bindEngineSignIns: HarnessBinder = ({
  container,
  dependencies,
  controlPlane,
  logger,
}) => {
  const signIns = dependencies.engineSignIns;
  if (
    signIns === undefined ||
    !container.isBound(AGENT_HARNESS_MODEL_ACCESS) ||
    container.isBound(AGENT_HARNESS_ENGINE_SIGNINS)
  ) {
    return;
  }
  const orgSwitch = boundOrNull<OrgSignInSwitch>(
    container,
    AGENT_HARNESS_ORG_SIGNIN_SWITCH,
  );
  container
    .bind<EngineSignInService>(AGENT_HARNESS_ENGINE_SIGNINS)
    .toConstantValue(
      new EngineSignInService({
        store: signIns.store,
        ...(signIns.relay !== undefined ? { relay: signIns.relay } : {}),
        access: container.get<ModelAccessService>(AGENT_HARNESS_MODEL_ACCESS),
        ...(orgSwitch !== null ? { orgSwitch } : {}),
        transaction: controlPlane,
        logger: logger ?? silentHarnessLogger,
      }),
    );
};

/** ENGPICK: bound only where engine ceilings, model access and a preference store all are. */
export const bindCodingEngines: HarnessBinder = ({
  container,
  dependencies,
  controlPlane,
  logger,
}) => {
  const preferences = dependencies.codingEnginePreferences;
  if (
    preferences === undefined ||
    !container.isBound(AGENT_HARNESS_ENGINE_PROFILES) ||
    !container.isBound(AGENT_HARNESS_MODEL_ACCESS) ||
    container.isBound(AGENT_HARNESS_CODING_ENGINES)
  ) {
    return;
  }
  const brokerEndpoint = dependencies.sandboxBrokerEndpoint;
  container
    .bind<CodingEngineService>(AGENT_HARNESS_CODING_ENGINES)
    .toDynamicValue(
      () =>
        new CodingEngineService({
          ceilings: container.get<EngineProfileCeilings>(
            AGENT_HARNESS_ENGINE_PROFILES,
          ),
          access: container.get<ModelAccessService>(AGENT_HARNESS_MODEL_ACCESS),
          preferences,
          transaction: controlPlane,
          ...(brokerEndpoint !== undefined
            ? {
                availability: new HttpEngineAvailability(brokerEndpoint, () =>
                  container
                    .get<HarnessAuthority>(AGENT_HARNESS_AUTHORITY)
                    .workerCredential(),
                ),
              }
            : {}),
          // Read at each use: the execution binder binds the sightings later.
          engineWorkers: {
            live: (feature) =>
              boundOrNull<WorkerFeatureAvailability>(
                container,
                AGENT_HARNESS_WORKER_FEATURES,
              )?.live(feature) ?? Promise.resolve(true),
          },
          logger: logger ?? silentHarnessLogger,
          ...(groupAccessOf(container) !== undefined
            ? { groupAccess: groupAccessOf(container) }
            : {}),
        }),
    )
    .inSingletonScope();
};
