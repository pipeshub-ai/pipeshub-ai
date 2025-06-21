export interface ValidationErrorDetail {
  field: string;
  message: string;
  value?: any;
  code: string;
}

export interface ValidatorOptions {
  abortEarly?: boolean;
  stripUnknown?: boolean;
}
