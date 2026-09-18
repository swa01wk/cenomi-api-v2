/** Portal GET /sales-data fields: property_name, company_name, brand_name. */

export type LeaseDisplayRow = {
  property_name?: string | null;
  mall_name?: string | null;
  company_name?: string | null;
  brand_name?: string | null;
};

export function salesDataProperty(row: LeaseDisplayRow): string {
  return (row.property_name || row.mall_name || "").trim() || "—";
}

export function salesDataCompany(row: LeaseDisplayRow): string {
  return (row.company_name || "").trim() || "—";
}

export function salesDataBrand(row: LeaseDisplayRow): string {
  return (row.brand_name || "").trim() || "—";
}
