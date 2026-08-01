import { markdownToSlackMrkdwn } from "./md_to_mrkdwn";
import { SlackActivityBuilder } from "./activity-ui";

const s =
  'I found the likely lease document: **"ScannedLEASEAGREEMENT (1)"**, concerning an educational property lease in Erode.';
console.log("converted:", markdownToSlackMrkdwn(s));

const builder = new SlackActivityBuilder();
builder.appendNarration(s);
builder.startTool("search", "Searched the knowledge base");
builder.finishTool("search", "Searched the knowledge base");
builder.setStatus("");
console.log("---activity---\n" + builder.format());
