// Country list for the eligibility home-country selector (Phase 4f-B).
//
// Covers the Erasmus+ programme countries plus the neighbouring partner
// regions that actually appear in our sources (Western Balkans, Eastern
// Partnership). ISO-3166-1 alpha-2, mirroring the codes the backend extractor
// validates against. Kept deliberately broad so a user from any of these can
// declare their home country; the `~ '^[A-Z]{2}$'` DB check is the backstop.
export const COUNTRIES: { code: string; name: string }[] = [
  { code: "AT", name: "Austria" },
  { code: "BE", name: "Belgium" },
  { code: "BG", name: "Bulgaria" },
  { code: "HR", name: "Croatia" },
  { code: "CY", name: "Cyprus" },
  { code: "CZ", name: "Czechia" },
  { code: "DK", name: "Denmark" },
  { code: "EE", name: "Estonia" },
  { code: "FI", name: "Finland" },
  { code: "FR", name: "France" },
  { code: "DE", name: "Germany" },
  { code: "GR", name: "Greece" },
  { code: "HU", name: "Hungary" },
  { code: "IE", name: "Ireland" },
  { code: "IS", name: "Iceland" },
  { code: "IT", name: "Italy" },
  { code: "LV", name: "Latvia" },
  { code: "LI", name: "Liechtenstein" },
  { code: "LT", name: "Lithuania" },
  { code: "LU", name: "Luxembourg" },
  { code: "MT", name: "Malta" },
  { code: "NL", name: "Netherlands" },
  { code: "NO", name: "Norway" },
  { code: "PL", name: "Poland" },
  { code: "PT", name: "Portugal" },
  { code: "RO", name: "Romania" },
  { code: "SK", name: "Slovakia" },
  { code: "SI", name: "Slovenia" },
  { code: "ES", name: "Spain" },
  { code: "SE", name: "Sweden" },
  { code: "TR", name: "Türkiye" },
  // Western Balkans
  { code: "AL", name: "Albania" },
  { code: "BA", name: "Bosnia and Herzegovina" },
  { code: "ME", name: "Montenegro" },
  { code: "MK", name: "North Macedonia" },
  { code: "RS", name: "Serbia" },
  { code: "XK", name: "Kosovo" },
  // Eastern Partnership
  { code: "AM", name: "Armenia" },
  { code: "AZ", name: "Azerbaijan" },
  { code: "GE", name: "Georgia" },
  { code: "MD", name: "Moldova" },
  { code: "UA", name: "Ukraine" },
  // Other
  { code: "GB", name: "United Kingdom" },
];

const VALID_CODES = new Set(COUNTRIES.map((c) => c.code));

export function isKnownCountry(code: string): boolean {
  return VALID_CODES.has(code);
}
