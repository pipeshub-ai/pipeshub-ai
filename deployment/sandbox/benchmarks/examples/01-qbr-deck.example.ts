// Reference implementation of benchmark prompt 01 (Q4 FY25 business review).
// Produces a 10-slide dark executive deck using pipeshub-slides so graders
// know what the agent should converge to. Run under the sandbox:
//
//     OUTPUT_DIR=/tmp/out npx tsx 01-qbr-deck.example.ts

import { Deck, THEMES } from "pipeshub-slides";
import * as path from "path";

const deck = new Deck({
  theme: THEMES.midnightExecutive,
  layout: "16x9",
  title: "Q4 FY25 Business Review",
  author: "pipeshub",
  company: "Q4 FY25 Review",
});

deck.titleSlide({
  eyebrow: "INTERNAL",
  title: "Q4 FY25",
  subtitle: "Business Review",
  author: "Prepared by Finance",
  date: "February 2026",
});

deck.sectionDivider({
  eyebrow: "Part 1",
  title: "By the numbers",
  subtitle: "The quarter in four headlines",
});

deck.statGrid({
  title: "By the numbers",
  stats: [
    { value: "+16%", label: "Revenue QoQ" },
    { value: "124%", label: "Net revenue retention" },
    { value: "32", label: "New logos" },
    { value: "4.8", label: "CSAT" },
  ],
});

deck.twoColumn({
  title: "Revenue trajectory",
  split: 50,
  left: {
    bullets: [
      "Q4 revenue landed at $67M (+16% QoQ, +18% YoY)",
      "Enterprise segment led the quarter at $41M",
      "Mid-market returned to growth for the first time in three quarters",
      "No material one-time effects; growth is durable",
    ],
  },
  right: {
    chart: {
      type: "bar",
      categories: ["Q1", "Q2", "Q3", "Q4"],
      series: [{ name: "Revenue ($M)", values: [42, 51, 58, 67] }],
    },
  },
});

deck.iconRows({
  title: "What shipped this quarter",
  rows: [
    {
      icon: "SSO",
      header: "Enterprise SSO (SAML + SCIM) GA",
      body: "Unblocks the top three pipeline logos",
    },
    {
      icon: "Audit",
      header: "Audit log GA",
      body: "All admin actions surfaced via API and streaming exports",
    },
    {
      icon: "Stream",
      header: "Streaming exports (50x faster)",
      body: "New Kafka-based pipeline; GA across all regions",
    },
  ],
});

deck.sectionDivider({
  eyebrow: "Part 2",
  title: "Risks",
  subtitle: "What we're watching for Q1",
});

deck.twoColumn({
  title: "Risks",
  left: {
    paragraph:
      "Pipeline coverage for enterprise is currently 2.6x — below our 3.0x " +
      "threshold. Two at-risk accounts represent >$1M ARR combined and are " +
      "each engaged in competitive renewals. Sales leadership has assigned " +
      "executive sponsors to both.",
  },
  right: {
    bullets: [
      "Enterprise pipeline coverage: 2.6x (target 3.0x)",
      "At-risk accounts: 2 (combined ARR $1.05M)",
      "Competitive renewals in Q1: 4",
      "Mitigations: exec sponsors, custom security review",
    ],
  },
});

deck.timeline({
  title: "FY25 milestones",
  steps: [
    { label: "Q1", description: "Hired new CRO" },
    { label: "Q2", description: "Launched self-serve tier" },
    { label: "Q3", description: "Shipped audit log private preview" },
    { label: "Q4", description: "Three major launches + 32 new logos" },
  ],
});

deck.twoColumn({
  title: "Looking ahead to Q1",
  left: {
    bullets: [
      "Focus: enterprise pipeline recovery",
      "Feature: BYOK GA, compliance export",
      "GTM: named-account team stood up",
      "Ops: reduce time-to-deploy by 40%",
    ],
  },
  right: {
    chart: {
      type: "line",
      categories: ["Jan", "Feb", "Mar"],
      series: [{ name: "Plan ($M)", values: [22, 25, 28] }],
    },
  },
});

deck.closing({
  title: "Thank you",
  subtitle: "Questions & discussion",
  cta: "finance@pipeshub.example",
});

const outPath = path.join(process.env.OUTPUT_DIR ?? "/tmp", "q4-review.pptx");
await deck.save(outPath);
