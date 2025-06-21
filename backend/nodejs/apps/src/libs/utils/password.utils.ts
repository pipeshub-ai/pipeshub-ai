export const passwordValidator = (password:string) => {
    // minimum 8 characters with minimum one uppercase, one lowercase, one number and one special character
    const passwordRegex = /^(?=.*?[A-Z])(?=.*?[a-z])(?=.*?[0-9])(?=.*?[#?!@$%^&*-]).{8,}$/;
    return passwordRegex.test(password);
  };
  