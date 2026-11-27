import type { Metadata } from "next";
import { Geist } from "next/font/google";

import { Providers } from "@/components/Providers";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  // latin-ext: Polish characters.
  subsets: ["latin", "latin-ext"],
});

export const metadata: Metadata = {
  title: "SkillLoop",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  // The default language; I18nProvider updates the attribute when another one is chosen.
  return (
    <html lang="pl" className={geistSans.variable}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
