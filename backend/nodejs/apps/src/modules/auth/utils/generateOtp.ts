import { randomInt } from "crypto";

export const generateOtp = () => {
    const digits = "0123456789";
    let otp = "";
  
    for (let i = 0; i < 6; i++) {
      otp += digits[randomInt(0, 10)];
    }
  
    return otp;
  };
  