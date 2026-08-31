import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DeFi Opportunity Intelligence",
  description: "Find yield. Understand the risk.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
