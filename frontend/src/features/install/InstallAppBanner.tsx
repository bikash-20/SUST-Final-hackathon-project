"use client";

import { useEffect, useState } from "react";
import Image from "next/image";

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
}

const DISMISSED_KEY = "liquiguard.install-banner-dismissed";

export function InstallAppBanner() {
  const [installPrompt, setInstallPrompt] = useState<InstallPromptEvent | null>(null);
  const [showIosHelp, setShowIosHelp] = useState(false);
  const [dismissed, setDismissed] = useState(true);

  useEffect(() => {
    const alreadyDismissed = window.localStorage.getItem(DISMISSED_KEY) === "true";
    if (alreadyDismissed) return;

    const navigatorWithStandalone = navigator as Navigator & { standalone?: boolean };
    const isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);
    const isStandalone =
      window.matchMedia("(display-mode: standalone)").matches ||
      navigatorWithStandalone.standalone === true;
    const revealTimer = window.setTimeout(() => {
      setDismissed(false);
      setShowIosHelp(isIos && !isStandalone);
    }, 0);

    const capturePrompt = (event: Event) => {
      event.preventDefault();
      setInstallPrompt(event as InstallPromptEvent);
    };
    window.addEventListener("beforeinstallprompt", capturePrompt);
    return () => {
      window.clearTimeout(revealTimer);
      window.removeEventListener("beforeinstallprompt", capturePrompt);
    };
  }, []);

  function dismiss() {
    window.localStorage.setItem(DISMISSED_KEY, "true");
    setDismissed(true);
  }

  async function install() {
    if (!installPrompt) return;
    await installPrompt.prompt();
    await installPrompt.userChoice;
    setInstallPrompt(null);
    dismiss();
  }

  if (dismissed || (!installPrompt && !showIosHelp)) return null;

  return (
    <aside className="fixed inset-x-3 bottom-3 z-50 mx-auto max-w-lg rounded-2xl border border-slate-700 bg-slate-950 p-3 text-white shadow-2xl shadow-slate-950/30 sm:bottom-5">
      <div className="flex items-center gap-3">
        <Image src="/icons/icon-192.png" alt="" width={44} height={44} className="rounded-xl" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">Keep LiquiGuard within reach</p>
          <p className="mt-0.5 text-xs leading-5 text-slate-300">
            {showIosHelp
              ? "On iPhone or iPad: tap Share, then Add to Home Screen."
              : "Install this app for a focused, app-like window. Network access is still required."}
          </p>
        </div>
        {installPrompt && (
          <button
            type="button"
            onClick={install}
            className="rounded-lg bg-emerald-400 px-3 py-2 text-xs font-bold text-slate-950 transition hover:bg-emerald-300"
          >
            Install
          </button>
        )}
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss install suggestion"
          className="grid h-8 w-8 place-items-center rounded-lg text-lg text-slate-400 transition hover:bg-white/10 hover:text-white"
        >
          ×
        </button>
      </div>
    </aside>
  );
}
