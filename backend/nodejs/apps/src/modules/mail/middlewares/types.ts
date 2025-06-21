export interface SmtpConfig {
  username?: string;
  password?: string;
  host: string;
  fromEmail: string;
  port: number;
}

export interface MailBody {
  productName?: string;
  emailTemplateType: string;
  isAutoEmail?: boolean;
  fromEmailDomain?: string;
  sendEmailTo?: string[];
  subject?: string;
  templateData?: Record<string, any>;
  sendCcTo?: string[];
  attachments?: any[];
}

export enum EmailTemplateType {
  LoginWithOtp = 'loginWithOTP',
  ResetPassword = 'resetPassword',
  AccountCreation = 'accountCreation',
  AppuserInvite = 'appuserInvite',
  SuspiciousLoginAttempt = 'suspiciousLoginAttempt',
}
