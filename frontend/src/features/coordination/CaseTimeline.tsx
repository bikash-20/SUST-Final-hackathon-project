"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

interface HistoryItem {
  type: "transition" | "note";
  timestamp: string;
  from?: string | null;
  to?: string | null;
  note_id?: string;
  author_role: string;
  text: string;
}

interface CaseHistoryResponse {
  case_id: string;
  status: string;
  history: HistoryItem[];
}

interface NoteResponse {
  note_id: string;
  case_id: string;
  author_role: string;
  note_text: string;
  timestamp: string;
}

async function readHistory(caseId: string): Promise<CaseHistoryResponse> {
  const response = await fetch(`/v1/cases/${caseId}/history`);
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<CaseHistoryResponse>;
}

export function CaseTimeline({
  caseId,
  authorRole,
}: {
  caseId: string;
  authorRole: "Ops" | "Risk Reviewer";
}) {
  const [open, setOpen] = useState(false);
  const [noteText, setNoteText] = useState("");
  const queryClient = useQueryClient();
  const history = useQuery({
    queryKey: ["case-history", caseId],
    queryFn: () => readHistory(caseId),
    enabled: open,
  });
  const addNote = useMutation({
    mutationFn: async (): Promise<NoteResponse> => {
      const response = await fetch(`/v1/cases/${caseId}/notes`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ author_role: authorRole, note_text: noteText.trim() }),
      });
      if (!response.ok) throw new Error(await response.text());
      return response.json() as Promise<NoteResponse>;
    },
    onSuccess: async () => {
      setNoteText("");
      await queryClient.invalidateQueries({ queryKey: ["case-history", caseId] });
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (noteText.trim()) addNote.mutate();
  }

  return (
    <details
      className="mt-3 rounded-xl border border-slate-200 bg-slate-50/80 p-3"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="cursor-pointer text-xs font-bold text-slate-700 marker:text-emerald-500">
        Notes and case history
      </summary>
      <div className="mt-2 space-y-2">
        {history.isLoading && <p className="text-xs text-slate-500">Loading history…</p>}
        {history.error && (
          <p className="text-xs text-rose-700">Could not load history: {history.error.message}</p>
        )}
        {history.data?.history.map((item, index) => (
          <div
            key={item.note_id ?? `${item.timestamp}-${item.to ?? item.type}-${index}`}
            className="rounded-r-lg border-l-2 border-emerald-300 bg-white/70 py-1.5 pl-2 pr-1 text-xs"
          >
            <div className="flex flex-wrap justify-between gap-1 text-[11px] text-slate-500">
              <span className="font-semibold">
                {item.type === "note"
                  ? `Note · ${item.author_role}`
                  : `${item.from ? `${item.from} → ` : ""}${item.to} · ${item.author_role}`}
              </span>
              <time dateTime={item.timestamp}>
                {item.timestamp ? new Date(item.timestamp).toLocaleString() : "Time unavailable"}
              </time>
            </div>
            {item.text && <p className="mt-0.5 text-slate-700">{item.text}</p>}
          </div>
        ))}
        <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row">
          <input
            value={noteText}
            onChange={(event) => setNoteText(event.target.value)}
            maxLength={2000}
            placeholder={`Add note as ${authorRole}`}
            aria-label={`Case note by ${authorRole}`}
            className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs outline-none transition focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/15"
          />
          <button
            type="submit"
            disabled={!noteText.trim() || addNote.isPending}
            className="rounded-lg bg-slate-900 px-3 py-2 text-xs font-bold text-white transition hover:bg-emerald-700 disabled:opacity-40"
          >
            Add
          </button>
        </form>
        {addNote.error && <p className="text-xs text-rose-700">{addNote.error.message}</p>}
      </div>
    </details>
  );
}
