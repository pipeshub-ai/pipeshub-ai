// ============================================================
// Mail / SMTP Types
// ============================================================

export interface SmtpConfig {
  host: string;
  port: number;
  fromEmail: string;
  username?: string;
  password?: string;
  inherited?: boolean;
  /** Absent means on; `false` means an admin turned email off. */
  enabled?: boolean;
}

export type SmtpFormData = {
  host: string;
  port: number | '';
  fromEmail: string;
  username: string;
  password: string;
};

export type SmtpFormErrors = {
  host?: string;
  port?: string;
  fromEmail?: string;
};
