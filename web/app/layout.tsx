import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "BranchLab — Change evidence",
  description: "Evidence-first AI regression investigations. Compare branches, trace behavior, inspect the proof.",
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
