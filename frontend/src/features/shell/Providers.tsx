"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useTelemetryStream } from "../telemetry/useTelemetryStream";
import { useThemeStore } from "./themeStore";

export function Providers({ children }: { children: ReactNode }) {
  useTelemetryStream();
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      })
  );

  // Re-sync the resolved theme whenever the OS preference flips while the
  // user is on "system", or whenever persisted state finishes hydrating.
  const mode = useThemeStore((s) => s.mode);
  const apply = useThemeStore((s) => s.applyToDocument);
  const setResolved = useThemeStore((s) => s.setResolved);
  useEffect(() => {
    apply();
    if (mode !== "system" || typeof window === "undefined") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => setResolved(media.matches ? "dark" : "light");
    media.addEventListener("change", handler);
    return () => media.removeEventListener("change", handler);
  }, [mode, apply, setResolved]);

  return (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}
