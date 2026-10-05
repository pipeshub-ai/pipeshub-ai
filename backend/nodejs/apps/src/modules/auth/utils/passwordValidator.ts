// bcrypt hashes only the first 72 bytes of the password and silently drops
// the rest, on hashing and on comparison alike. A longer password would be
// accepted here, stored truncated, and then match any input sharing its first
// 72 bytes — the complexity the regex demanded might sit entirely in the part
// that was thrown away.
export const BCRYPT_MAX_PASSWORD_BYTES = 72;

export const passwordValidator = (password: string): boolean => {
  if (Buffer.byteLength(password, 'utf8') > BCRYPT_MAX_PASSWORD_BYTES) {
    return false;
  }
  // minimum 8 characters with minimum one uppercase, one lowercase, one number and one special character
  const passwordRegex =
    /^(?=.*?[A-Z])(?=.*?[a-z])(?=.*?[0-9])(?=.*?[#?!@$%^&*-]).{8,}$/;
  return passwordRegex.test(password);
};
