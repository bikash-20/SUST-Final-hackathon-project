"use client";
/** Persistent role + provider tab state. Survives reloads. */
import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Role = "agent" | "ops" | "risk";
export type ProviderTab = "bkash" | "nagad" | "rocket";

interface RoleState {
  role: Role;
  setRole: (r: Role) => void;
  provider: ProviderTab;
  setProvider: (p: ProviderTab) => void;
}

export const useRoleStore = create<RoleState>()(
  persist(
    (set) => ({
      role: "ops",
      provider: "bkash",
      setRole: (r) => set({ role: r }),
      setProvider: (p) => set({ provider: p }),
    }),
    { name: "liquiguard.role" }
  )
);
