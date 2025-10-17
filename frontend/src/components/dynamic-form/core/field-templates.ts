import { z } from 'zod';
import keyIcon from '@iconify-icons/mdi/key';
import linkIcon from '@iconify-icons/mdi/link';
import robotIcon from '@iconify-icons/mdi/robot';
import serverIcon from '@iconify-icons/mdi/server';
import cubeIcon from '@iconify-icons/mdi/cube-outline';
import mailLineIcon from '@iconify-icons/ri/mail-line';
import mediaIcon from '@iconify-icons/mdi/image';
import brainIcon from '@iconify-icons/mdi/brain';
import type { IconifyIcon } from '@iconify/react';

export interface FieldTemplate {
  name: string;
  label: string;
  placeholder?: string;
  type?: 'text' | 'password' | 'email' | 'number' | 'url' | 'select' | 'checkbox' | 'file';
  icon?: string | IconifyIcon;
  required?: boolean;
  validation?: z.ZodType;
  gridSize?: { xs?: number; sm?: number; md?: number };
  options?: { value: string; label: string }[];
  multiline?: boolean;
  rows?: number;
  acceptedFileTypes?: string[];
  maxFileSize?: number;
  fileProcessor?: (data: any) => any;
  [key: string]: any;
}

export const FIELD_TEMPLATES = {
  // AUTHENTICATION FIELDS
  apiKey: {
    name: 'apiKey',
    label: 'API Key',
    type: 'password' as const,
    placeholder: 'Your API Key',
    icon: keyIcon,
    required: true,
    validation: z.string().min(1, 'API Key is required'),
  },
  
  clientId: {
    name: 'clientId',
    label: 'Client ID',
    placeholder: 'Your Client ID',
    icon: keyIcon,
    required: true,
    validation: z.string().min(1, 'Client ID is required'),
  },

  clientSecret: {
    name: 'clientSecret',
    label: 'Client Secret',
    type: 'password' as const,
    placeholder: 'Your Client Secret',
    icon: keyIcon,
    required: true,
    validation: z.string().min(1, 'Client Secret is required'),
  },

  // MODEL FIELDS
  model: {
    name: 'model',
    label: 'Model Name',
    placeholder: 'Model name',
    icon: robotIcon,
    required: true,
    validation: z.string().min(1, 'Model is required'),
  },

  // ENDPOINT FIELDS
  endpoint: {
    name: 'endpoint',
    label: 'Endpoint URL',
    type: 'url' as const,
    placeholder: 'https://api.example.com/v1',
    icon: linkIcon,
    required: true,
    validation: z.string().min(1, 'Endpoint is required').url('Must be a valid URL'),
  },

  deploymentName: {
    name: 'deploymentName',
    label: 'Deployment Name',
    placeholder: 'Your deployment name',
    icon: cubeIcon,
    required: true,
    validation: z.string().min(1, 'Deployment Name is required'),
  },

  // EMAIL FIELDS
  fromEmail: {
    name: 'fromEmail',
    label: 'From Email Address',
    type: 'email' as const,
    placeholder: 'notifications@yourdomain.com',
    icon: mailLineIcon,
    required: true,
    validation: z.string().email('Invalid email address').min(1, 'From email is required'),
  },

  // SERVER FIELDS
  host: {
    name: 'host',
    label: 'SMTP Host',
    placeholder: 'smtp.gmail.com',
    icon: serverIcon,
    required: true,
    validation: z.string().min(1, 'Host is required'),
    gridSize: { xs: 12, sm: 8 },
  },

  port: {
    name: 'port',
    label: 'Port',
    type: 'number' as const,
    placeholder: '587',
    icon: serverIcon,
    required: false,
    validation: z.number().min(1).max(65535).default(587),
    gridSize: { xs: 12, sm: 4 },
  },

  username: {
    name: 'username',
    label: 'Username (Optional)',
    placeholder: 'your.username',
    icon: keyIcon,
    required: false,
    validation: z.string().optional(),
    gridSize: { xs: 12, sm: 6 },
  },

  password: {
    name: 'password',
    label: 'Password (Optional)',
    type: 'password' as const,
    placeholder: 'Your password',
    icon: keyIcon,
    required: false,
    validation: z.string().optional(),
    gridSize: { xs: 12, sm: 6 },
  },

  // MODEL OPTIONS
  isMultimodal: {
    name: 'isMultimodal',
    label: 'Multimodal',
    type: 'checkbox' as const,
    placeholder: 'Supports (text + image)',
    icon: mediaIcon,
    required: false,
    validation: z.boolean().optional().default(true),
    gridSize: { xs: 12, sm: 6 },
  },

  isReasoning: {
    name: 'isReasoning',
    label: 'Reasoning',
    type: 'checkbox' as const,
    placeholder: 'Supports reasoning',
    icon: brainIcon,
    required: false,
    validation: z.boolean().optional().default(false),
    gridSize: { xs: 12, sm: 6 },
  },

  // URL FIELDS
  frontendUrl: {
    name: 'frontendUrl',
    label: 'Frontend URL',
    type: 'url' as const,
    placeholder: 'https://yourdomain.com',
    icon: linkIcon,
    required: true,
    validation: z.string().refine((val) => val === '' || /^https?:\/\/.+/.test(val), 'Please enter a valid URL'),
  },

  connectorUrl: {
    name: 'connectorUrl',
    label: 'Connector URL',
    type: 'url' as const,
    placeholder: 'https://connector.yourdomain.com',
    icon: linkIcon,
    required: true,
    validation: z.string().refine((val) => val === '' || /^https?:\/\/.+/.test(val), 'Please enter a valid URL'),
  },

  // STORAGE FIELDS - S3
  s3AccessKeyId: {
    name: 's3AccessKeyId',
    label: 'Access Key ID',
    placeholder: 'AKIAIOSFODNN7EXAMPLE',
    icon: keyIcon,
    required: true,
    validation: z.string().min(1, 'S3 access key ID is required'),
  },

  s3SecretAccessKey: {
    name: 's3SecretAccessKey',
    label: 'Secret Access Key',
    type: 'password' as const,
    placeholder: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
    icon: keyIcon,
    required: true,
    validation: z.string().min(1, 'S3 secret access key is required'),
  },

  s3Region: {
    name: 's3Region',
    label: 'Region',
    placeholder: 'us-east-1',
    icon: serverIcon,
    required: true,
    validation: z.string().min(1, 'S3 region is required'),
    gridSize: { xs: 12, sm: 6 },
  },

  s3BucketName: {
    name: 's3BucketName',
    label: 'Bucket Name',
    placeholder: 'my-bucket',
    icon: cubeIcon,
    required: true,
    validation: z.string().min(1, 'S3 bucket name is required'),
    gridSize: { xs: 12, sm: 6 },
  },

  // STORAGE FIELDS - AZURE BLOB
  accountName: {
    name: 'accountName',
    label: 'Account Name',
    placeholder: 'mystorageaccount',
    icon: keyIcon,
    required: true,
    validation: z.string().min(1, 'Azure account name is required'),
    gridSize: { xs: 12, sm: 6 },
  },

  accountKey: {
    name: 'accountKey',
    label: 'Account Key',
    type: 'password' as const,
    placeholder: 'Your account key',
    icon: keyIcon,
    required: true,
    validation: z.string().min(1, 'Azure account key is required'),
  },

  containerName: {
    name: 'containerName',
    label: 'Container Name',
    placeholder: 'my-container',
    icon: cubeIcon,
    required: true,
    validation: z.string().min(1, 'Azure container name is required'),
    gridSize: { xs: 12, sm: 6 },
  },

  endpointProtocol: {
    name: 'endpointProtocol',
    label: 'Protocol',
    type: 'select' as const,
    icon: serverIcon,
    required: false,
    validation: z.enum(['http', 'https']).default('https'),
    gridSize: { xs: 12, sm: 6 },
    options: [
      { value: 'https', label: 'HTTPS' },
      { value: 'http', label: 'HTTP' },
    ]as { value: string; label: string }[],
  },

  endpointSuffix: {
    name: 'endpointSuffix',
    label: 'Endpoint Suffix (Optional)',
    placeholder: 'core.windows.net',
    icon: serverIcon,
    required: false,
    validation: z.string().optional().default('core.windows.net'),
    gridSize: { xs: 12, sm: 6 },
  },

  // LOCAL STORAGE FIELDS
  mountName: {
    name: 'mountName',
    label: 'Mount Name (Optional)',
    placeholder: 'my-mount',
    icon: cubeIcon,
    required: false,
    validation: z.string().optional(),
    gridSize: { xs: 12, sm: 6 },
  },

  baseUrl: {
    name: 'baseUrl',
    label: 'Base URL (Optional)',
    type: 'url' as const,
    placeholder: 'http://localhost:3000/files',
    icon: linkIcon,
    required: false,
    validation: z.string().optional().or(z.literal('')).refine(
      (val) => {
        if (!val || val.trim() === '') return true;
        try {
          const url = new URL(val);
          return !!url;
        } catch {
          return false;
        }
      },
      { message: 'Must be a valid URL' }
    ),
    gridSize: { xs: 12, sm: 6 },
  },

  // AWS BEDROCK FIELDS
  region: {
    name: 'region',
    label: 'Region',
    placeholder: 'us-east-1',
    icon: serverIcon,
    required: true,
    validation: z.string().min(1, 'Region is required'),
  },

  accessKeyId: {
    name: 'accessKeyId',
    label: 'Access Key ID',
    type: 'password' as const,
    placeholder: 'AKIAIOSFODNN7EXAMPLE',
    icon: keyIcon,
    required: true,
    validation: z.string().min(1, 'Access Key ID is required'),
  },

  secretAccessKey: {
    name: 'secretAccessKey',
    label: 'Secret Access Key',
    type: 'password' as const,
    placeholder: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
    icon: keyIcon,
    required: true,
    validation: z.string().min(1, 'Secret Access Key is required'),
  },

  // ADDITIONAL FIELDS
  organizationId: {
    name: 'organizationId',
    label: 'Organization ID',
    placeholder: 'org-xxxxxxxxxxxxxxxxxxxxxxxx',
    icon: cubeIcon,
    required: false,
    validation: z.string().optional(),
  },

  projectId: {
    name: 'projectId',
    label: 'Project ID',
    placeholder: 'your-project-id',
    icon: cubeIcon,
    required: false,
    validation: z.string().optional(),
  },

  awsAccessKeyId: {
    name: 'awsAccessKeyId',
    label: 'AWS Access Key ID',
    type: 'password' as const,
    placeholder: 'AKIAIOSFODNN7EXAMPLE',
    icon: keyIcon,
    required: true,
    validation: z.string().min(1, 'AWS Access Key ID is required'),
  },

  awsAccessSecretKey: {
    name: 'awsAccessSecretKey',
    label: 'AWS Access Secret Key',
    type: 'password' as const,
    placeholder: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
    icon: keyIcon,
    required: true,
    validation: z.string().min(1, 'AWS Access Secret Key is required'),
  },

  provider: {
    name: 'provider',
    label: 'Provider',
    placeholder: 'anthropic',
    icon: serverIcon,
  },
} as const;

export type FieldTemplateName = keyof typeof FIELD_TEMPLATES;