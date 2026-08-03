import { markdownToSlackMrkdwn } from "./md_to_mrkdwn";
import {
  rewriteCitationsForSlack,
  stripTinyRefCitationLinks,
  type SlackCitationLike,
} from "./citations";

function assert(condition: boolean, message: string): void {
  if (!condition) {
    throw new Error(message);
  }
}

const frontendBaseUrl = "http://localhost:3000";

const rawSourceRefs =
  "Property of approximately 3.54 acres [source](ref219) [source](ref224). Annual rent: ₹2,000 [source](ref19).";

const stripped = stripTinyRefCitationLinks(rawSourceRefs);
assert(!stripped.includes("ref219"), "strip removes ref219");
assert(!stripped.includes("[source]"), "strip removes [source] links");
assert(stripped.includes("3.54 acres"), "strip keeps prose");

const mrkdwnWithoutStrip = markdownToSlackMrkdwn(rawSourceRefs);
assert(
  mrkdwnWithoutStrip.includes("<ref219|source>") ||
    mrkdwnWithoutStrip.includes("ref219"),
  "sanity: raw source refs become broken Slack links without rewrite",
);

const rewrittenEmpty = rewriteCitationsForSlack(rawSourceRefs, [], frontendBaseUrl);
const mrkdwnRewrittenEmpty = markdownToSlackMrkdwn(rewrittenEmpty);
assert(!mrkdwnRewrittenEmpty.includes("ref219"), "rewrite strips tiny refs");
assert(!mrkdwnRewrittenEmpty.includes("|source>"), "no <…|source> after rewrite");

const citations: SlackCitationLike[] = [
  {
    citationData: {
      chunkIndex: 1,
      metadata: {
        recordId: "rec-1",
        recordType: "FILE",
        connector: "LOCAL",
        webUrl: "/record/rec-1/preview#blockIndex=5",
      },
    },
  },
  {
    citationData: {
      chunkIndex: 2,
      metadata: {
        recordId: "rec-2",
        recordType: "FILE",
        connector: "LOCAL",
        webUrl: "/record/rec-2/preview#blockIndex=9",
      },
    },
  },
];

const numbered =
  "Lease term is 33 years [1](/record/rec-1/preview#blockIndex=5). Advance ₹10,000 [2](ref93).";
const rewritten = rewriteCitationsForSlack(numbered, citations, frontendBaseUrl);
assert(
  rewritten.includes("[1](http://localhost:3000/record/rec-1)"),
  "maps chunkIndex 1 to frontend record URL",
);
assert(!rewritten.includes("ref93"), "drops unmapped tiny-ref numbered link");
assert(
  !markdownToSlackMrkdwn(rewritten).includes("|source>"),
  "numbered rewrite never emits <…|source>",
);

const prenormalized = "Institutional activities [1](ref93) [2](https://ref95.xyz).";
const prenormalizedRewritten = rewriteCitationsForSlack(
  prenormalized,
  citations,
  frontendBaseUrl,
);
assert(
  prenormalizedRewritten.includes("[1](http://localhost:3000/record/rec-1)"),
  "prenormalized [1](ref…) uses citation map URL",
);
assert(
  prenormalizedRewritten.includes("[2](http://localhost:3000/record/rec-2)"),
  "prenormalized [2](https://ref….xyz) uses citation map URL",
);

console.log("citations.selftest: ok");
