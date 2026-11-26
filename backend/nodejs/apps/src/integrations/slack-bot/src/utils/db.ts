import { config } from 'dotenv';

// Loads .env before anything here connects, as this module did before the repository layer.
config({ quiet: true });

export {
  connect,
  dropLegacyThreadBotIndex,
} from '../../../../libs/repositories/slack-thread';
