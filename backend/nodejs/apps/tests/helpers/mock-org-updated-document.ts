import sinon from 'sinon';

import { Org } from '../../src/modules/user_management/schema/org.schema';

/**
 * Plain org JSON matching `OrgDocumentResponseSchema` and org `data` in
 * update/delete success responses; use with Mongoose-like `toJSON()` in tests.
 */
export function createMockOrgUpdateJson(
  overrides: Record<string, unknown> = {},
) {
  return {
    _id: '507f1f77bcf86cd799439012',
    registeredName: 'Example Org',
    shortName: 'EO',
    domain: 'example.com',
    contactEmail: 'contact@example.com',
    accountType: 'business',
    onBoardingStatus: 'configured',
    isDeleted: false,
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    slug: 'org-1',
    __v: 0,
    ...overrides,
  };
}

/** Mongoose-like document with `toJSON()` for org update controller tests. */
export function createMockOrgUpdatedDocument(
  overrides: Record<string, unknown> = {},
) {
  const plain = createMockOrgUpdateJson(overrides);
  return {
    ...plain,
    toJSON: () => plain,
  };
}

/**
 * Document for soft-delete tests: `isDeleted` is toggled by the controller;
 * `toJSON()` reflects the current flag (must be `true` in the HTTP response).
 */
export function createMockOrgDocumentForSoftDelete(
  overrides: Record<string, unknown> = {},
) {
  const base = createMockOrgUpdateJson({ isDeleted: false, ...overrides });
  const doc: Record<string, unknown> = {
    ...base,
    isDeleted: false,
    toJSON() {
      return createMockOrgUpdateJson({
        ...base,
        isDeleted: doc.isDeleted as boolean,
      });
    },
  };
  return doc;
}

/**
 * Stubs `Org.prototype.toJSON` so `sendValidatedJson(OrgDocumentResponseSchema, …)`
 * receives a full wire-shaped org when tests stub `save()` (pre-save hooks and
 * timestamps may not run). Call from `createOrg` tests; `sinon.restore()` clears it.
 */
export function stubOrgPrototypeToJsonForApiContract(): sinon.SinonStub {
  return sinon.stub(Org.prototype, 'toJSON').callsFake(function (
    this: Record<string, unknown>,
  ) {
    const d = this;
    const rawId = d._id;
    const id =
      rawId != null &&
      typeof (rawId as { toString?: () => string }).toString === 'function'
        ? String((rawId as { toString: () => string }).toString())
        : String(rawId ?? '');

    const nowIso = new Date().toISOString();
    const toIso = (v: unknown) => {
      if (v instanceof Date) return v.toISOString();
      if (typeof v === 'string') return v;
      return nowIso;
    };

    const out: Record<string, unknown> = {
      _id: id,
      registeredName:
        typeof d.registeredName === 'string' ? d.registeredName : '',
      domain: d.domain as string,
      contactEmail: d.contactEmail as string,
      accountType: d.accountType as string,
      onBoardingStatus: (d.onBoardingStatus as string) ?? 'notConfigured',
      isDeleted: Boolean(d.isDeleted),
      createdAt: toIso(d.createdAt),
      updatedAt: toIso(d.updatedAt),
      slug:
        typeof d.slug === 'string' && d.slug.length > 0
          ? d.slug
          : 'org-test-stub',
      __v: typeof d.__v === 'number' ? d.__v : 0,
    };

    if (d.shortName !== undefined) {
      out.shortName = d.shortName;
    }
    if (d.permanentAddress !== undefined) {
      out.permanentAddress = d.permanentAddress;
    }

    return out;
  });
}
