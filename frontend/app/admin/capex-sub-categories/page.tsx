import type { Metadata } from "next";
import { MasterDataPage } from "../_registers/master-data-page";

export const metadata: Metadata = {
  title: "CAPEX sub-categories | TRANSPIRE Administration",
  description: "Manage the sub-categories people choose for Capex Projects.",
};

export default function Page() {
  return <MasterDataPage kind="capex_subtype" />;
}
