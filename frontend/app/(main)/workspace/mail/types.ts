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
