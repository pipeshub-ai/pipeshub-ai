/**
 * Where Slack's Web API is reached: `SLACK_API_URL` when a deployment sets it (a proxy, or the e2e lanes' scripted
 * workspace), else Slack's own. The bot and Node's own Slack messages read the same setting.
 */
export function slackClientOptions(): { slackApiUrl?: string } {
  const url = process.env.SLACK_API_URL?.trim();
  if (url === undefined || url === '') return {};
  return { slackApiUrl: url.endsWith('/') ? url : `${url}/` };
}
