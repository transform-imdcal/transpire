import type { Metadata } from "next";
import { ChartersRegister } from "./charters-register";

export const metadata: Metadata = {
  title: "Charters | TRANSPIRE",
  description: "Every project charter in the workspace, being drafted or baselined.",
};

export default function ChartersPage() {
  return <ChartersRegister />;
}
