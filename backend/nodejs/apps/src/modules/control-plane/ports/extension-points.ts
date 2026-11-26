import type { Problem } from '../contracts/generated';
import type { CatalogItem, Descriptor } from '../domain/catalog-item';
import type {
  Binding,
  ChangeKind,
  Installation,
  UnavailableReason,
} from '../domain/objects';
import type { DimensionRow } from '../domain/policy-alias';
import type { PinSubject } from '../domain/resolution';

/**
 * The extension points of 03-interfaces §3.1, one capability each (ISP): a
 * domain module implements only the ones it has. The core depends on the
 * individual points, never on a kind.
 */

/**
 * Where a kind's items come from. `releaseItems` is build-time data;
 * `descriptorDigest` recomputes one of the kind's descriptors, so only the
 * module knows its descriptor shape. `importItem` (org rows) arrives with the
 * import WP.
 */
export interface CatalogSource {
  readonly kind: string;
  releaseItems(): readonly CatalogItem[];
  descriptorDigest(descriptor: Descriptor): string;
}

export interface ValidationContext {
  readonly orgId: string;
  readonly actor: string;
}

/** Validates an installation's configuration for a kind. */
export interface ConfigValidator {
  readonly kind: string;
  validate(
    item: CatalogItem,
    config: unknown,
    ctx: ValidationContext,
  ): Promise<Problem[]>;
}

export interface DescriptorSet {
  readonly descriptors: readonly Descriptor[];
  readonly digest: string;
}

/** What an installation exposes, with digests (admin time; Python drivers behind it). */
export interface DescriptorProvider {
  readonly kind: string;
  /** `credential` is a binding id, or null to describe without one. */
  describe(
    inst: Installation,
    credential: string | null,
  ): Promise<DescriptorSet>;
}

/** Which changes of an installation need configuration-time approval. */
export interface ApprovalTriggers {
  readonly kind: string;
  changesOf(before: Installation | null, after: Installation): ChangeKind[];
}

/** The bindings of one installation the snapshot holds. */
export type BindingIndex = readonly Binding[];

export type BindingChoice =
  | { readonly binding: Binding }
  | {
      readonly unavailable: Extract<UnavailableReason, `credential_${string}`>;
    };

/** Which binding a run uses for an installation. */
export interface BindingSelector {
  readonly kind: string;
  select(
    inst: Installation,
    subject: PinSubject,
    bindings: BindingIndex,
  ): BindingChoice;
}

/** Policy rows the domain adds (data, validated against the dimension table's schema). */
export interface DimensionContributor {
  readonly kind: string;
  dimensions(): readonly DimensionRow[];
}

/** Plugins: an install that creates child installations. */
export interface InstallExpander {
  readonly kind: string;
  expand(inst: Installation, item: CatalogItem): Promise<Installation[]>;
}

/**
 * One domain's registration bundle. `validator` and `approvals` are optional
 * until every production module brings them (CPD-7, CPD-9); the harness module
 * has no `descriptors`, skills no `bindings`, only plugins an `expander`.
 */
export interface DomainModule {
  readonly kind: string;
  readonly catalog: CatalogSource;
  readonly validator?: ConfigValidator;
  readonly descriptors?: DescriptorProvider;
  readonly approvals?: ApprovalTriggers;
  readonly bindings?: BindingSelector;
  readonly dimensions?: DimensionContributor;
  readonly expander?: InstallExpander;
}
