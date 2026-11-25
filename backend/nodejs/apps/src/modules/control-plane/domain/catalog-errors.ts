/** Catalog refusals; `reason` is the stable code a route maps to a `Problem` (CPD-11). */
export abstract class CatalogError extends Error {
  abstract readonly reason: string;

  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}

export class InvalidItemRef extends CatalogError {
  readonly reason = 'invalid_item_ref';
}

export class InvalidCatalogItem extends CatalogError {
  readonly reason = 'invalid_catalog_item';
}

export class DuplicateDescriptorName extends CatalogError {
  readonly reason = 'duplicate_descriptor_name';
}

export class ItemExists extends CatalogError {
  readonly reason = 'catalog_item_exists';
}

export class DigestMismatch extends CatalogError {
  readonly reason = 'catalog_digest_mismatch';
}

export class ItemNotFound extends CatalogError {
  readonly reason = 'catalog_item_not_found';
}

export class SharedItemReadOnly extends CatalogError {
  readonly reason = 'catalog_item_read_only';
}

export class OrgMismatch extends CatalogError {
  readonly reason = 'catalog_org_mismatch';
}

export class UnknownKind extends CatalogError {
  readonly reason = 'catalog_unknown_kind';
}

export class InvalidReleaseItem extends CatalogError {
  readonly reason = 'catalog_release_item_invalid';
}
