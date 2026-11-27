/**
 * Values the shared tests read here instead of hard-coding, where a build of the product may differ (the Node twin
 * of backend/python/tests/support/edition_defaults.py). A build that differs replaces this one file.
 */

/** A Slack member without a PipesHub account is served under the scoped token Node mints for them. */
export const SLACK_CALLER_WITHOUT_ACCOUNT_REFUSED = false;
