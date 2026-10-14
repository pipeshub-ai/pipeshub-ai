import 'reflect-metadata';
import { expect } from 'chai';
import mongoose from 'mongoose';
import {
  SERVICE_ACCOUNT_EMAIL_DOMAIN,
  buildServiceAccountEmail,
  isServiceAccountEmail,
  isValidServiceAccountSlug,
  serviceAccountSlugFromEmail,
} from '../../../../src/modules/user_management/constants/service-account.constants';

describe('service-account.constants', () => {
  const orgId = new mongoose.Types.ObjectId().toString();

  describe('isValidServiceAccountSlug', () => {
    it('accepts lowercase words joined by single hyphens', () => {
      expect(isValidServiceAccountSlug('nightly-sync')).to.equal(true);
      expect(isValidServiceAccountSlug('jira2')).to.equal(true);
      expect(isValidServiceAccountSlug('a-b-c-d')).to.equal(true);
    });

    it('rejects a slug that would make a malformed address', () => {
      expect(isValidServiceAccountSlug('-leading')).to.equal(false);
      expect(isValidServiceAccountSlug('trailing-')).to.equal(false);
      expect(isValidServiceAccountSlug('double--hyphen')).to.equal(false);
      expect(isValidServiceAccountSlug('has space')).to.equal(false);
      expect(isValidServiceAccountSlug('has@at')).to.equal(false);
      expect(isValidServiceAccountSlug('UPPER')).to.equal(false);
    });

    it('rejects slugs outside the length bounds', () => {
      expect(isValidServiceAccountSlug('ab')).to.equal(false);
      expect(isValidServiceAccountSlug('a'.repeat(49))).to.equal(false);
    });
  });

  describe('buildServiceAccountEmail', () => {
    it('puts the account under the reserved domain', () => {
      const email = buildServiceAccountEmail('nightly-sync', orgId);
      expect(email.endsWith(`@${SERVICE_ACCOUNT_EMAIL_DOMAIN}`)).to.equal(true);
    });

    it('includes the org so two orgs can use the same name', () => {
      const otherOrgId = new mongoose.Types.ObjectId().toString();
      expect(buildServiceAccountEmail('nightly-sync', orgId)).to.not.equal(
        buildServiceAccountEmail('nightly-sync', otherOrgId),
      );
    });
  });

  describe('serviceAccountSlugFromEmail', () => {
    it('recovers the slug the address was built from', () => {
      for (const slug of ['nightly-sync', 'jira2', 'a-b-c-d']) {
        const email = buildServiceAccountEmail(slug, orgId);
        expect(serviceAccountSlugFromEmail(email, orgId)).to.equal(slug);
      }
    });
  });

  describe('isServiceAccountEmail', () => {
    it('recognises the reserved domain and nothing else', () => {
      expect(
        isServiceAccountEmail(buildServiceAccountEmail('sync', orgId)),
      ).to.equal(true);
      expect(isServiceAccountEmail('person@example.com')).to.equal(false);
      expect(isServiceAccountEmail(undefined)).to.equal(false);
      expect(isServiceAccountEmail('')).to.equal(false);
    });

    it('does not match a lookalike domain owned by someone else', () => {
      expect(
        isServiceAccountEmail('svc-x@service.pipeshub.internal.example.com'),
      ).to.equal(false);
    });
  });
});
