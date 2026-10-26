import sinon from 'sinon';
import * as edition from '../../src/edition';

/** Sets the edition's sign-in lists for each test in the calling describe. */
export function useSignInLists(
  removed: readonly string[],
  renamed: Readonly<Record<string, string>>,
): void {
  let sandbox: sinon.SinonSandbox;
  beforeEach(() => {
    sandbox = sinon.createSandbox();
    sandbox.replace(edition, 'REMOVED_SIGN_IN_METHODS', removed);
    sandbox.replace(edition, 'RENAMED_SIGN_IN_METHODS', renamed);
  });
  afterEach(() => sandbox.restore());
}

/** The community edition's lists, so these tests hold on any edition's build. */
export function useCommunitySignInLists(): void {
  useSignInLists(['samlSso', 'oauth'], { azureAd: 'microsoft' });
}
