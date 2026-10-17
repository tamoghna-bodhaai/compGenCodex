"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { isActiveGeneration } from "@/lib/logic";
import type { IngestionJob, PaperSummary } from "@/lib/types";

interface ToastItem { id: number; message: string; tone: "default" | "error" }

interface WorkspaceValue {
  role: "admin" | "teacher" | "student";
  papers: PaperSummary[];
  ingestionJobs: IngestionJob[];
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
  setIngestionJob: (job: IngestionJob) => void;
  toast: (message: string, tone?: "default" | "error") => void;
}

const WorkspaceContext = createContext<WorkspaceValue | null>(null);

export function WorkspaceProvider({ children, role }: { children: React.ReactNode; role: "admin" | "teacher" | "student" }) {
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [ingestionJobs, setIngestionJobs] = useState<IngestionJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const toastId = useRef(0);
  const refreshController = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    refreshController.current?.abort();
    const controller = new AbortController();
    refreshController.current = controller;
    try {
      const [paperResult, ingestionResult] = await Promise.all([role === "student" ? Promise.resolve({ items: [] }) : api.papers(controller.signal), role === "admin" ? api.ingestionJobs(controller.signal) : Promise.resolve({ items: [] })]);
      if (controller.signal.aborted) return;
      setPapers(paperResult.items);
      setIngestionJobs(ingestionResult.items);
      setError("");
    } catch (caught) {
      if (controller.signal.aborted) return;
      setError(caught instanceof Error ? caught.message : "Couldn’t reach the API.");
    } finally {
      if (refreshController.current === controller) {
        refreshController.current = null;
        setLoading(false);
      }
    }
  }, [role]);

  useEffect(() => {
    void refresh();
    return () => refreshController.current?.abort();
  }, [refresh]);

  const active = papers.some(isActiveGeneration) || ingestionJobs.some((job) => ["queued", "running"].includes(job.state));
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(timer);
  }, [active, refresh]);

  const setIngestionJob = useCallback((job: IngestionJob) => {
    setIngestionJobs((items) => [job, ...items.filter((item) => item.id !== job.id)]);
  }, []);

  const toast = useCallback((message: string, tone: "default" | "error" = "default") => {
    const id = ++toastId.current;
    setToasts((items) => [...items, { id, message, tone }]);
    window.setTimeout(() => setToasts((items) => items.filter((item) => item.id !== id)), 4200);
  }, []);

  const value = useMemo(() => ({ role, papers, ingestionJobs, loading, error, refresh, setIngestionJob, toast }), [role, papers, ingestionJobs, loading, error, refresh, setIngestionJob, toast]);

  return (
    <WorkspaceContext.Provider value={value}>
      {children}
      <div className="toastRegion" aria-live="polite">
        {toasts.map((item) => <div key={item.id} className={`toast ${item.tone === "error" ? "toastError" : ""}`}>{item.message}</div>)}
      </div>
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return value;
}
