import {
  liveLayerOf,
  type HeadRevisions,
  type LiveLayer,
  type PolicyLayer,
  type RepositoryPolicy,
  type WebsitePolicy,
} from '../../control-plane';

export interface LiveLayerServiceOptions {
  heads: HeadRevisions;
  /** The org's stored layers by scope; absent: no policy store is wired, nothing stored narrows. */
  layers?: (orgId: string) => Promise<ReadonlyMap<string, PolicyLayer>>;
  /** The org's kill switch; absent: the deployment has no Labs settings to read. */
  killSwitch?: (orgId: string) => Promise<boolean>;
  /** The deployment's cap on allowed websites (WP-63, `HARNESS_ALLOWED_WEBSITES_CAP`); null or absent: none. */
  websitesCap?: WebsitePolicy | null;
  /** The deployment's cap on allowed git hosts (WP-63 part A, `HARNESS_ALLOWED_REPOSITORIES_CAP`); null or absent: none. */
  repositoriesCap?: RepositoryPolicy | null;
  /** Org-layer sections an edition carries in the live layer (`PolicySectionRule.live`); absent: none. */
  carried?: readonly string[];
}

/**
 * What a worker or a gateway narrows running work by (02-lld §8.3): the
 * org's live layer at its head. The head is read first, so the layers are
 * never older than the revision they are answered under; a write landing in
 * between is answered early and its event then finds nothing to change.
 * Anything unreadable throws: a holder keeps its last layer and fails closed
 * when that is too old (NFR-33), never reads "nothing narrows".
 */
export class LiveLayerService {
  constructor(private readonly options: LiveLayerServiceOptions) {}

  /** `null`: the org head is still `since`, so the holder's layer is current. */
  async read(orgId: string, since?: number): Promise<LiveLayer | null> {
    const revision = await this.options.heads.head(orgId);
    if (since !== undefined && since === revision) return null;
    const [layers, killSwitch] = await Promise.all([
      this.options.layers?.(orgId) ??
        Promise.resolve(new Map<string, PolicyLayer>()),
      this.options.killSwitch?.(orgId) ?? Promise.resolve(false),
    ]);
    return liveLayerOf({
      orgId,
      revision,
      layers,
      killSwitch,
      websitesCap: this.options.websitesCap ?? null,
      repositoriesCap: this.options.repositoriesCap ?? null,
      ...(this.options.carried !== undefined
        ? { carried: this.options.carried }
        : {}),
    });
  }
}
