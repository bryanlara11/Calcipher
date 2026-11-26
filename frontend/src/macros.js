// Protein, carbs and fat that a record actually reports. Missing values are left out of the UI
// rather than shown as blanks; calories are always present and shown separately.
export const MACRO_FIELDS = [["Protein", "protein_g"], ["Carbs", "carbs_g"], ["Fat", "fat_g"]];

export function knownMacros(item) {
  return MACRO_FIELDS
    .filter(([, field]) => item?.[field] != null && item[field] !== "" && Number.isFinite(Number(item[field])))
    .map(([label, field]) => [label, Number(item[field])]);
}

export const formatGrams = value => `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} g`;
