/**
 * Every sign-in and refresh writes a session family (ADR-WEB-02 D9). A unit test has no database, so each test starts
 * with an empty in-memory store with the same compare-and-set answers; a test that wants to look at it installs its
 * own with `setSessionFamilyRepository`. Loaded by `.mocharc.yaml`.
 */
import { setSessionFamilyRepository } from '../../src/libs/repositories/session-family';
import { InMemorySessionFamilies } from '../support/auth/in-memory-session-families';

export const mochaHooks = {
  beforeEach(): void {
    setSessionFamilyRepository(new InMemorySessionFamilies());
  },
};
