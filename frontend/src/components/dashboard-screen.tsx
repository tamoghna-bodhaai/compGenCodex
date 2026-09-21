"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { downloadPaper, api } from "@/lib/api";
import { paperDashboardState } from "@/lib/logic";
import type { PaperSummary } from "@/lib/types";
import { useWorkspace } from "@/context/workspace-context";
import { Button, EmptyState, IngestionProgress, JobProgress, PageHeader, StatusBadge, generationLabel } from "@/components/ui";
import { Icon } from "@/components/icons";
import { ReferenceReadyReckoner } from "@/components/reference-ready-reckoner";
import s from "@/styles/ui.module.css";

const FILTERS = [["all", "All papers"], ["draft", "Drafts"], ["generating", "Generating"], ["ready", "Ready"], ["attention", "Needs attention"], ["cancelled", "Cancelled"]] as const;

function label(paper: PaperSummary) {
  const state = paperDashboardState(paper);
  if (state === "generating") return "Generating";
  if (state === "cancelled") return "Cancelled";
  if (state === "attention") return "Needs attention";
  if (state === "ready") return paper.status === "final" ? "Final" : "Ready";
  return "Draft";
}

export function DashboardScreen() {
  const { papers, ingestionJobs, loading, error, refresh, toast } = useWorkspace();
  const [filter, setFilter] = useState<(typeof FILTERS)[number][0]>("all");
  const [search, setSearch] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [referenceOpen, setReferenceOpen] = useState(false);
  const [referenceSubmitting, setReferenceSubmitting] = useState(false);
  const [controllingPaperId, setControllingPaperId] = useState<string | null>(null);
  const [deletingPaperId, setDeletingPaperId] = useState<string | null>(null);

  const metrics = useMemo(() => papers.reduce((result, paper) => {
    result.total += 1; result.questions += paper.question_count || 0;
    const state = paperDashboardState(paper);
    if (state === "generating") result.generating += 1;
    if (state === "ready") result.ready += 1;
    return result;
  }, { total: 0, questions: 0, generating: 0, ready: 0 }), [papers]);

  const filtered = useMemo(() => papers.filter((paper) => {
    const query = search.trim().toLowerCase();
    const matchesQuery = !query || [paper.title, paper.exam, paper.subject].some((value) => value.toLowerCase().includes(query));
    return matchesQuery && (filter === "all" || paperDashboardState(paper) === filter);
  }), [papers, search, filter]);
  const displayed = showAll ? filtered : filtered.slice(0, 9);
  const tracked = papers.filter((paper) => ["generating", "attention"].includes(paperDashboardState(paper)));
  const trackedIngestion = ingestionJobs.filter((job) => ["queued", "running", "failed"].includes(job.state));

  async function retry(paper: PaperSummary) {
    try {
      const endpoint = paper.generation_job?.state === "failed" && paper.generation_job.operation === "solutions" ? `/papers/${paper.id}/solutions/generate` : `/papers/${paper.id}/generate`;
      await api.post(endpoint); await refresh(); toast("Generation queued. Track it in Live activity.");
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Generation failed.", "error"); }
  }
  async function controlGeneration(paper: PaperSummary, action: "pause" | "resume" | "cancel") {
    setControllingPaperId(paper.id);
    try {
      if (action === "cancel" && !window.confirm("Cancel this generation? Completed work will be kept and this update will be removed from Live activity.")) return;
      await api.post(`/papers/${paper.id}/generation/${action}`);
      await refresh();
      if (action === "pause") toast("Generation paused. Completed questions are ready to review.");
      else if (action === "resume") toast("Generation resumed.");
      else toast("Generation cancelled. It has been removed from Live activity and kept in history.");
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t update generation.", "error"); }
    finally { setControllingPaperId(null); }
  }
  async function exportPdf(paper: PaperSummary) {
    try { await downloadPaper(paper, "pdf", "question_paper"); toast("PDF download started."); }
    catch (caught) { toast(caught instanceof Error ? caught.message : "Export failed.", "error"); }
  }
  async function dismissIngestionUpdate(jobId: string) {
    try {
      await api.deleteIngestionJob(jobId);
      await refresh();
      toast("Ingestion update deleted.");
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t delete the ingestion update.", "error"); }
  }
  async function deletePaper(paper: PaperSummary) {
    if (!window.confirm(`Delete “${paper.title}”? This permanently removes the paper, its questions, and generation history.`)) return;
    setDeletingPaperId(paper.id);
    try {
      await api.deletePaper(paper.id);
      await refresh();
      toast("Paper deleted.");
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t delete the paper.", "error"); }
    finally { setDeletingPaperId(null); }
  }

  return <section className={s.content}>
    <PageHeader eyebrow="Overview" title="Your paper workspace" description="Create, monitor, curate, and export JEE-ready question papers from one focused workspace." actions={<><Link className={`${s.button} ${s.button_default}`} href="/question-bank"><Icon name="library" />Browse question bank</Link><Button tone="default" icon="sparkle" onClick={() => setReferenceOpen(true)}>Generate from reference</Button><Link className={`${s.button} ${s.button_primary}`} href="/new-paper"><Icon name="plus" />Create paper</Link></>} />

    <section className={s.metricStrip} aria-label="Paper overview">
      {[{ key: "all", label: "Total papers", value: metrics.total, icon: "document" as const }, { key: "generating", label: "Generating", value: metrics.generating, icon: "sparkle" as const }, { key: "ready", label: "Ready to export", value: metrics.ready, icon: "check" as const }, { key: "all", label: "Total questions", value: metrics.questions, icon: "questions" as const }].map((item, index) => <button key={`${item.label}-${index}`} className={`${s.metric} ${filter === item.key ? s.metricActive : ""}`} onClick={() => { setFilter(item.key as typeof filter); setShowAll(false); }}><span className={s.metricIcon}><Icon name={item.icon} /></span><span><small>{item.label}</small><strong>{item.value}</strong></span></button>)}
    </section>

    {(referenceSubmitting || tracked.length > 0 || trackedIngestion.length > 0) && <section className={s.panel} aria-labelledby="activity-heading"><div className={s.sectionHead}><div><span className={s.eyebrow}>Live activity</span><h2 id="activity-heading">Generation &amp; ingestion</h2></div><span className={s.autoUpdate}><span className={`${s.liveDot} ${s.pulse}`} />Updates automatically</span></div><div className={s.activityGrid}>{referenceSubmitting && <article className={s.activityCard} aria-live="polite"><div className={s.activityTitle}><div><strong>Reference paper</strong><StatusBadge status="generating">Preparing</StatusBadge></div></div><div className={s.ingestionProgress}><div className={s.ingestionStatus}><span className={`${s.liveDot} ${s.pulse}`} /><div><div className={s.ingestionTitle}><strong>Uploading &amp; extracting questions</strong></div><span>Your reference is being prepared for generation.</span></div></div></div></article>}{tracked.map((paper) => { const active = paperDashboardState(paper) === "generating"; const paused = paper.generation_job?.control_state === "paused"; const hasPartialPaper = paper.question_count > 0 && active; return <article className={s.activityCard} key={paper.id}><div className={s.activityTitle}><div><strong>{paper.title}</strong><StatusBadge status={paperDashboardState(paper)}>{label(paper)}</StatusBadge></div><Link href={`/papers/${paper.id}`}>{hasPartialPaper ? "View partial paper" : "Open"} <Icon name="arrow" /></Link></div>{paper.generation_job && <JobProgress job={paper.generation_job} onPause={active && !paused && controllingPaperId !== paper.id ? () => void controlGeneration(paper, "pause") : undefined} onResume={active && paused && controllingPaperId !== paper.id ? () => void controlGeneration(paper, "resume") : undefined} onCancel={active && controllingPaperId !== paper.id ? () => void controlGeneration(paper, "cancel") : undefined} />}{paperDashboardState(paper) === "attention" && <Button size="small" onClick={() => void retry(paper)}>Retry generation</Button>}</article>; })}{trackedIngestion.map((job) => <article className={s.activityCard} key={job.id}><div className={s.activityTitle}><strong>{job.source_name}</strong><StatusBadge status={job.state === "failed" ? "attention" : "generating"}>{job.state === "failed" ? "Needs attention" : "Ingesting"}</StatusBadge></div><IngestionProgress job={job} />{job.state === "failed" && <Button tone="danger" size="small" onClick={() => { if (window.confirm("Delete this ingestion update? Imported questions will not be deleted.")) void dismissIngestionUpdate(job.id); }}>Delete update</Button>}</article>)}</div></section>}

    <section className={`${s.panel} ${s.libraryPanel}`} aria-labelledby="paper-library-heading">
      <div className={s.sectionHead}><div><span className={s.eyebrow}>Library</span><h2 id="paper-library-heading">Your papers <span className={s.countBadge}>{papers.length}</span></h2></div><div className={s.libraryHeadActions}>{filtered.length > displayed.length && <Button tone="quiet" size="small" onClick={() => setShowAll(true)}>View all {filtered.length}<Icon name="arrow" /></Button>}<label className={s.search}><span className={s.srOnly}>Search papers</span><Icon name="search" /><input value={search} onChange={(event) => { setSearch(event.target.value); setShowAll(false); }} placeholder="Search papers, exams, or subjects" /></label></div></div>
      <div className={s.filterbar}><div className={s.segmented} role="tablist" aria-label="Filter papers by status">{FILTERS.map(([value, text]) => <button key={value} role="tab" aria-selected={filter === value} className={filter === value ? s.segmentActive : ""} onClick={() => { setFilter(value); setShowAll(false); }}>{text}</button>)}</div><span>{displayed.length} of {filtered.length} shown</span></div>
      {loading ? <EmptyState title="Loading your workspace" description="Fetching papers and current generation activity…" /> : error ? <EmptyState title="Couldn’t reach the API" description={`${error} Start FastAPI on port 8000, then retry.`} action={<Button onClick={() => void refresh()}>Retry</Button>} /> : displayed.length ? <div className={s.paperTable} role="table" aria-label="Paper library"><div className={s.paperTableHead} role="row"><span>Paper</span><span>Status</span><span>Questions</span><span>Updated</span><span className={s.srOnly}>Actions</span></div>{displayed.map((paper) => { const state = paperDashboardState(paper); const requested = paper.requested_question_count || 0; const isPartial = state === "generating" && paper.question_count > 0; const deleting = deletingPaperId === paper.id; return <article className={s.paperRow} role="row" key={paper.id}><div className={s.paperIdentity}><span className={s.paperIcon}><Icon name="document" /></span><span><strong>{paper.title}</strong><small>{[paper.exam, paper.subject].filter(Boolean).join(" · ") || "Paper workspace"}</small></span></div><StatusBadge status={state}>{label(paper)}</StatusBadge><span className={s.rowMeta}>{requested ? `${paper.question_count}/${requested}` : paper.question_count} questions{state === "generating" && <small>{generationLabel(paper)}</small>}</span><span className={s.rowMeta}>Updated {new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(new Date(paper.updated_at))}</span><div className={s.rowActions}>{state === "attention" || state === "cancelled" || (state === "draft" && !paper.question_count) ? <Button size="small" disabled={deleting} onClick={() => void retry(paper)}>{state === "attention" ? "Retry" : state === "cancelled" ? "Continue" : "Generate"}</Button> : <Link className={`${s.button} ${s.button_primary} ${s.buttonSmall}`} href={`/papers/${paper.id}`}>{isPartial ? "View partial" : "Open"} <Icon name="arrow" /></Link>}{paper.question_count > 0 && <Button tone="quiet" size="small" icon="download" disabled={deleting} aria-label={`Export ${paper.title} as PDF`} onClick={() => void exportPdf(paper)} />}<Button tone="danger" size="small" disabled={deleting} onClick={() => void deletePaper(paper)}>{deleting ? "Deleting…" : "Delete"}</Button></div></article>; })}</div> : <EmptyState icon={papers.length ? "search" : "document"} title={papers.length ? "No papers match this view" : "Your paper library starts here"} description={papers.length ? "Try another status or clear your search." : "Create a paper, track its generation, and return here whenever you need it."} action={papers.length ? <Button onClick={() => { setFilter("all"); setSearch(""); }}>Show all papers</Button> : <Link className={`${s.button} ${s.button_primary}`} href="/new-paper">Create your first paper</Link>} />}
    </section>
    <ReferenceReadyReckoner open={referenceOpen} onClose={() => setReferenceOpen(false)} onSubmissionChange={setReferenceSubmitting} />
  </section>;
}
