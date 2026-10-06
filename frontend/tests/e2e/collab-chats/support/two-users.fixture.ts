import { test as base, expect, type BrowserContext, type Page } from '@playwright/test';
import { FRONTEND_ORIGIN, NodeApi, fake, stackState, type Actor } from './stack';

/** What `lib/store/auth-store.ts` reads on boot; the same keys the legacy frontend used. */
const ACCESS_TOKEN_KEY = 'jwt_access_token';
const REFRESH_TOKEN_KEY = 'jwt_refresh_token';

/** Storage state that signs a roster user in without the login form: the app accepts any JWT the Node API verifies. */
export function storageStateFor(actor: Actor) {
  return {
    cookies: [],
    origins: [
      {
        origin: new URL(FRONTEND_ORIGIN).origin,
        localStorage: [
          { name: ACCESS_TOKEN_KEY, value: actor.token },
          { name: REFRESH_TOKEN_KEY, value: actor.token },
        ],
      },
    ],
  };
}

interface Person {
  actor: Actor;
  context: BrowserContext;
  page: Page;
  api: NodeApi;
}

interface TwoUsers {
  /** The chat owner (roster `owner`). */
  a: Person;
  /** The collaborator (roster `write_recipient`). */
  b: Person;
  /** The viewer (roster `read_recipient`): the third user of the journeys. */
  c: Person;
  newPerson: (rosterKey: 'read_recipient' | 'stranger' | 'admin' | 'team_writer' | 'team_reader' | 'project_viewer' | 'project_editor') => Promise<Person>;
  /** A brand-new user (own rate-limit budget and owner-status memory), signed in in a context of their own. */
  fresh: (label: string) => Promise<Person>;
}

export const test = base.extend<{ users: TwoUsers }>({
  users: async ({ browser }, use, testInfo) => {
    const { roster } = stackState();
    await fake.reset();
    const opened: BrowserContext[] = [];
    // A request that got no response surfaces in the app as a generic "Network error"; keep Chromium's reason.
    const failedRequests: string[] = [];
    const person = async (actor: Actor): Promise<Person> => {
      // Separate contexts: separate localStorage, so each user keeps their own tokens and slots.
      const context = await browser.newContext({ storageState: storageStateFor(actor), viewport: { width: 1280, height: 900 } });
      opened.push(context);
      context.on('requestfailed', (r) => {
        failedRequests.push(`${new Date().toISOString()} ${actor.email} ${r.method()} ${r.url()} ${r.failure()?.errorText ?? ''}`);
      });
      return { actor, context, page: await context.newPage(), api: new NodeApi(actor) };
    };
    const a = await person(roster.owner);
    const b = await person(roster.write_recipient);
    const c = await person(roster.read_recipient);
    const people = [a, b, c];
    await use({
      a,
      b,
      c,
      newPerson: async (key) => {
        const p = await person(roster[key]);
        people.push(p);
        return p;
      },
      fresh: async (label) => {
        const p = await person(await fake.freshActor(label));
        people.push(p);
        return p;
      },
    });
    if (testInfo.status !== testInfo.expectedStatus && failedRequests.length > 0) {
      await testInfo.attach('failed-requests', { body: failedRequests.join('\n'), contentType: 'text/plain' });
    }
    await Promise.all(people.map((p) => p.api.deleteCreated()));
    await fake.reset();
    await Promise.all(opened.map((c) => c.close()));
  },
});

export { expect };
