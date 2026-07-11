"use client";
import { useRoleStore, type Role } from "./roleStore";

/** Select only the role value so callers receive a typed string, not the store. */
export function useRole(): Role {
  return useRoleStore((state) => state.role);
}
