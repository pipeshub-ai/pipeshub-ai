export function deriveNameFromEmail(email?: string): {
    firstName: string;
    lastName: string;
    fullName: string;
} {
    // 1. Handle missing or invalid email
    if (!email || !email.includes("@")) {
        return { firstName: "User", lastName: "", fullName: "User" };
    }

    const localPart = email.split("@").shift();

    if (!localPart) {
        return { firstName: "User", lastName: "", fullName: "User" };
    }

    // 2. Clean trailing numbers and split by common delimiters
    const cleaned = localPart.replace(/\d+$/, "");
    const parts = cleaned.split(/[._-]/).filter(Boolean);

    // 3. Format first and last name
    const firstName = parts[0]
        ? parts[0].charAt(0).toUpperCase() + parts[0].slice(1).toLowerCase()
        : "User";

    const lastName = parts[1]
        ? parts[1].charAt(0).toUpperCase() + parts[1].slice(1).toLowerCase()
        : "";

    const fullName = `${firstName} ${lastName}`.trim();

    return {
        firstName,
        lastName,
        fullName,
    };
}