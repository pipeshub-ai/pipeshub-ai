import { jurisdictions } from "./juridiction.utils";
export interface Address{
    addressLine1?: string;
    city?: string;
    state?: string;
    postCode?: string;
    country?: jurisdictions;
}