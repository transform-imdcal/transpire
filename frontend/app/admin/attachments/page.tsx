import type { Metadata } from "next";
import { AttachmentPolicyConsole } from "./attachment-policy-console";

export const metadata: Metadata = {
  title: "Attachments | TRANSPIRE Administration",
  description: "Decide which files people can attach to ideas, and how many.",
};

export default function Page() {
  return <AttachmentPolicyConsole />;
}
