/** Slack's native status rotates while the model is working and clears on reply. */
const THINKING_MESSAGES = ['Thinking through your request…', 'Working on your response…'];

export class SlackNativeStatus {
  private active = false;

  constructor(
    private readonly apiCall: (method: string, args: Record<string, unknown>) => Promise<unknown>,
    private readonly channel: string,
    private readonly threadTs: string,
  ) {}

  async start(): Promise<boolean> {
    try {
      const result = await this.apiCall('assistant.threads.setStatus', {
        channel_id: this.channel,
        thread_ts: this.threadTs,
        status: 'is thinking…',
        loading_messages: THINKING_MESSAGES,
      });
      this.active = !(result && typeof result === 'object' && 'ok' in result && result.ok === false);
      return this.active;
    } catch {
      this.active = false;
      return false;
    }
  }

  async update(status: string): Promise<boolean> {
    if (!this.active) return false;
    try {
      const result = await this.apiCall('assistant.threads.setStatus', {
        channel_id: this.channel,
        thread_ts: this.threadTs,
        status: status ? `is ${status.charAt(0).toLowerCase()}${status.slice(1)}` : '',
        ...(status.toLowerCase().startsWith('thinking') ? { loading_messages: THINKING_MESSAGES } : {}),
      });
      if (result && typeof result === 'object' && 'ok' in result && result.ok === false) throw new Error('Slack status rejected');
      return true;
    } catch {
      this.active = false;
      return false;
    }
  }

  async clear(): Promise<void> {
    if (this.active) await this.update('');
    this.active = false;
  }
}
