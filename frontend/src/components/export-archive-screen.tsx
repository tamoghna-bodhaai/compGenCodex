"use client";

import { useEffect, useState } from "react";
import { downloadArchivedExport, api } from "@/lib/api";
import type { PaperExport } from "@/lib/types";
import { EmptyState, PageHeader } from "@/components/ui";
import { Icon } from "@/components/icons";
import s from "@/styles/ui.module.css";

function bytes(value: number) {
  return value < 1024 * 1024 ? `${Math.max(1, Math.round(value / 1024))} KB` : `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function ExportArchiveScreen() {
  const [items, setItems] = useState<PaperExport[]>([]);
  const [error, setError] = useState("");
  useEffect(() => { const controller = new AbortController(); void api.exports(undefined, controller.signal).then((result) => setItems(result.items)).catch((caught) => { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Could not load exports."); }); return () => controller.abort(); }, []);
  return <section className={s.content}>
    <PageHeader eyebrow="Persistent storage" title="Export archive" description="PDF, DOCX, and TeX files retained on the secure API volume." />
    {error ? <p className={s.errorText}>{error}</p> : items.length === 0 ? <EmptyState icon="archive" title="No archived exports" description="New exports and migrated legacy documents will appear here." /> : <div className={s.archiveList}>
      {items.map((item) => <article key={item.id} className={s.archiveItem}><div><strong>{item.filename}</strong><small>{item.kind === "legacy" ? "Legacy archive" : "Paper export"} · {bytes(item.byte_size)} · {new Date(item.created_at).toLocaleDateString()}</small></div><button className={s.button} type="button" onClick={() => downloadArchivedExport(item)}><Icon name="download" />Download</button></article>)}
    </div>}
  </section>;
}
