import { UnknownKind } from '../domain/catalog-errors';
import type {
  CatalogSource,
  ConfigValidator,
  DomainModule,
} from '../ports/extension-points';

/** Composition registers one module per domain; the core asks it for a point by kind. */
export class ExtensionRegistry {
  private readonly modules = new Map<string, DomainModule>();

  register(module: DomainModule): void {
    if (this.modules.has(module.kind))
      throw new Error(`domain module ${module.kind} is already registered`);
    if (module.catalog.kind !== module.kind) {
      throw new Error(
        `domain module ${module.kind} brings a catalog source for ${module.catalog.kind}`,
      );
    }
    this.modules.set(module.kind, module);
  }

  catalogSource(kind: string): CatalogSource {
    const module = this.modules.get(kind);
    if (module === undefined)
      throw new UnknownKind(`no domain module for kind ${kind}`);
    return module.catalog;
  }

  /** The kind's configuration validator; null when its module brings none (its installs are made on its own page). */
  validator(kind: string): ConfigValidator | null {
    return this.modules.get(kind)?.validator ?? null;
  }

  kinds(): readonly string[] {
    return [...this.modules.keys()];
  }
}
