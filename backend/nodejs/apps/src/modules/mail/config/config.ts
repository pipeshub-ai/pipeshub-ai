export interface MailConfig {
  jwtSecret: string;
  scopedJwtSecret: string;
  database: {
    url: string;
    dbName: string;
  };
  smtp: {
    username: string;
    password: string;
    host: string;
    port: number;
    fromEmail: string;
  };
}
