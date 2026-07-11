import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "@/features/shell/Providers";
import { Shell } from "@/features/shell/Shell";
import { InstallAppBanner } from "@/features/install/InstallAppBanner";

export const metadata: Metadata = {
  title: "LiquiGuard — Multi-Provider Decision Support",
  applicationName: "LiquiGuard",
  description:
    "Liquidity, anomaly, and coordination decision support for bKash, Nagad, and Rocket operations.",
  manifest: "/manifest.json",
  icons: {
    icon: [
      { url: "/icons/favicon-16.png", sizes: "16x16", type: "image/png" },
      { url: "/icons/favicon-32.png", sizes: "32x32", type: "image/png" },
      { url: "/icons/liquiguard-mark.svg", type: "image/svg+xml" },
    ],
    apple: [{ url: "/icons/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
  appleWebApp: {
    capable: true,
    title: "LiquiGuard",
    statusBarStyle: "black-translucent",
  },
};

export const viewport = {
  themeColor: "#0f172a",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="bn">
      <body className="min-h-screen text-slate-900 antialiased">
        <Providers>
          <Shell>{children}</Shell>
          <InstallAppBanner />
        </Providers>
      </body>
    </html>
  );
}
