export const generateOtp = () => {
    const digits = "0123456789";
    let otp = "";
  
    for (let i = 0; i < 6; i++) {
      otp += digits[Math.floor(Math.random() * 10)];
    }
  
    return otp;
  };
  