declare module "fernet" {
  interface FernetToken {
    encode(message: string): string;
    decode(token: string): string;
  }

  interface FernetInstance {
    ttl: number;
    secret: unknown;
    setSecret(secret64: string): unknown;
    Token: new (opts: {
      secret?: unknown;
      ttl?: number;
      iv?: string;
      message?: string;
      token?: string;
      time?: string;
    }) => FernetToken;
  }

  interface FernetConstructor {
    new (opts?: { ttl?: number; secret?: string; iv?: string }): FernetInstance;
  }

  const Fernet: FernetConstructor;
  export = Fernet;
}
