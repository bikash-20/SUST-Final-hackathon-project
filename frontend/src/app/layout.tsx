import type { Metadata } from "next";
import { Inter, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { Providers } from "@/features/shell/Providers";
import { Shell } from "@/features/shell/Shell";
import { InstallAppBanner } from "@/features/install/InstallAppBanner";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  variable: "--font-plex-mono",
});

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

const THEME_BOOT = `
(function () {
  try {
    var raw = localStorage.getItem("liquiguard.theme");
    var mode = "light";
    if (raw) {
      var parsed = JSON.parse(raw);
      mode = parsed && parsed.state && parsed.state.mode ? parsed.state.mode : "light";
    }
    var resolved = mode === "dark" ? "dark" : mode === "system"
      ? (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      : "light";
    if (resolved === "dark") document.documentElement.classList.add("dark");
    document.documentElement.style.colorScheme = resolved;
  } catch (_) { /* default to light */ }
})();
`.trim();

export const viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0B0F14" },
    { media: "(prefers-color-scheme: light)", color: "#f8fafc" },
  ],
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="bn" className={`${inter.variable} ${plexMono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body className="min-h-screen bg-base font-sans text-ink antialiased">
        <Providers>
          <Shell>{children}</Shell>
          <InstallAppBanner />
        </Providers>
      </body>
    </html>
  );
}
