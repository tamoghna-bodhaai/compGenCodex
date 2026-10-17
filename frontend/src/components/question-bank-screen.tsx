"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { availability, DIFFICULTIES, QUESTION_TYPES } from "@/lib/logic";
import type { BankSelection, CatalogRow, IngestionJob } from "@/lib/types";
import { useWorkspace } from "@/context/workspace-context";
import { Dialog } from "@/components/dialog";
import { QuestionBrowserDialog } from "@/components/question-browser-dialog";
import { Button, EmptyState, Field, IngestionProgress, PageHeader, Textarea } from "@/components/ui";
import s from "@/styles/ui.module.css";

function unique<T>(items: T[], key: (item: T) => string) { return [...new Map(items.map((item) => [key(item), item])).values()]; }

export function QuestionBankScreen({ view = "bank" }: { view?: "bank" | "ingestion" }) {
  const { role, ingestionJobs, loading: workspaceLoading, error: workspaceError, setIngestionJob, refresh, toast } = useWorkspace();
  const [catalog, setCatalog] = useState<CatalogRow[]>([]);
  const [selection, setSelection] = useState<BankSelection>({});
  const [bankFilter, setBankFilter] = useState<BankSelection>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [browserOpen, setBrowserOpen] = useState(false);
  const [ingestionOpen, setIngestionOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const catalogRequest = useRef<AbortController | null>(null);

  const loadCatalog = useCallback(async () => { catalogRequest.current?.abort(); const controller = new AbortController(); catalogRequest.current = controller; try { setCatalog((await api.catalog(controller.signal)).items); setError(""); } catch (caught) { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Couldn’t load the catalog."); } finally { if (catalogRequest.current === controller) { catalogRequest.current = null; setLoading(false); } } }, []);
  useEffect(() => { void loadCatalog(); return () => catalogRequest.current?.abort(); }, [loadCatalog]);

  const openBranch = (next: BankSelection) => { setSelection({ ...bankFilter, ...next }); setBrowserOpen(true); };
  const activeJobs = ingestionJobs.filter((job) => ["queued", "running"].includes(job.state));
  const reviewCount = ingestionJobs.reduce((total, job) => total + (job.review_questions || 0), 0);
  const completedJobIds = ingestionJobs.filter((job) => job.state === "succeeded").map((job) => job.id).join(",");
  useEffect(() => { if (completedJobIds) void loadCatalog(); }, [completedJobIds, loadCatalog]);

  const collectionTotal = availability(catalog);
  const branches = useMemo(() => {
    const examRows = unique(catalog.filter((row) => row.exam), (row) => row.exam);
    return examRows.map((examRow) => {
      const exam = examRow.exam; const withinExam = catalog.filter((row) => row.exam === exam);
      return { exam, count: availability(withinExam), subjects: unique(withinExam, (row) => row.subject).map((subjectRow) => { const subject = subjectRow.subject; const withinSubject = withinExam.filter((row) => row.subject === subject); return { subject, count: availability(withinSubject), chapters: unique(withinSubject.filter((row) => row.chapter), (row) => row.chapter || "").map((chapterRow) => { const chapter = chapterRow.chapter || ""; const withinChapter = withinSubject.filter((row) => row.chapter === chapter); const topics = unique(withinChapter.filter((row) => row.topic), (row) => row.topic || "").map((topicRow) => { const topic = topicRow.topic || ""; const withinTopic = withinChapter.filter((row) => row.topic === topic); return { topic, count: availability(withinTopic), topicOnlyCount: availability(withinTopic.filter((row) => !row.subtopic)), subtopics: unique(withinTopic.filter((row) => row.subtopic), (row) => row.subtopic || "").map((row) => ({ subtopic: row.subtopic || "", count: availability(withinTopic.filter((item) => item.subtopic === row.subtopic)) })) }; }); return { chapter, count: availability(withinChapter), topics }; }) }; }) };
    });
  }, [catalog]);

  async function ingest(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (submitting) return; setSubmitting(true);
    const values = new FormData(event.currentTarget); const file = values.get("file") as File | null; const text = String(values.get("source_text") || "").trim();
    if (file && file.size > 35 * 1024 * 1024) { toast("Uploads must be 35 MB or smaller.", "error"); setSubmitting(false); return; }
    if ((!file || !file.size) && !text) { toast("Upload a PDF/DOCX or paste question text.", "error"); setSubmitting(false); return; }
    for (const name of ["file", "answer_key_file", "solutions_file"]) {
      const upload = values.get(name);
      if (upload instanceof File && !upload.name && !upload.size) values.delete(name);
    }
    try {
      const result = await api.ingest(values);
      setIngestionJob(result.job);
      setIngestionOpen(false);
      toast("Ingestion accepted. Track its progress here or on the dashboard.");
    }
    catch (caught) { toast(caught instanceof Error ? caught.message : "Ingestion failed.", "error"); }
    finally { setSubmitting(false); }
  }
  async function controlIngestion(job: IngestionJob, action: "pause" | "resume" | "cancel") {
    try {
      if (action === "cancel" && !window.confirm("Cancel this ingestion? Accepted questions and the report will be retained.")) return;
      const result = action === "pause" ? await api.pauseIngestion(job.id) : action === "resume" ? await api.resumeIngestion(job.id) : await api.cancelIngestion(job.id);
      setIngestionJob(result.job);
      toast(action === "pause" ? "Ingestion paused." : action === "resume" ? "Ingestion resumed." : "Ingestion cancelled; completed work was retained.");
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t update ingestion.", "error"); }
  }
  async function removeSource(id: string) {
    if (!window.confirm("Permanently delete this source, its questions, attachments, and diagrams? Generated papers remain saved.")) return;
    try { await api.deleteIngestionJob(id); await Promise.all([loadCatalog(), refresh()]); toast("Source deleted."); }
    catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t delete source.", "error"); }
  }

  if (view === "bank" && loading) return <section className={s.content}><EmptyState title="Loading the question bank" description="Reading the classified seed catalog…" /></section>;
  if (view === "ingestion" && workspaceLoading) return <section className={s.content}><EmptyState title="Loading sources" description="Checking ingestion activity…" /></section>;
  return <section className={`${s.content} ${s.bankContent}`}>
    {view === "bank" ? <>
      <PageHeader eyebrow="Question bank" title="Browse questions" description="Explore classified questions by subject and topic, or use filters to find a specific set." actions={<><Link className={`${s.button} ${s.button_default}`} href="/new-paper">Create paper</Link>{role === "admin" && <Link className={`${s.button} ${s.button_primary}`} href="/ingestion">Ingest questions</Link>}</>} />
      {error ? <EmptyState title="Couldn’t load the question bank" description={error} action={<Button onClick={() => void loadCatalog()}>Retry</Button>} /> : collectionTotal ? <>
      <section className={s.bankFilters}><div className={s.bankFiltersHead}><div><span className={s.eyebrow}>Find questions</span><h2>Filter the library</h2></div><span>Choose filters, then open matching questions</span></div><div className={s.bankFilterGrid}><label>Source <select className={s.input} value={bankFilter.source_job_id || ""} onChange={(event) => setBankFilter((current) => ({ ...current, source_job_id: event.target.value || undefined }))}><option value="">All sources</option>{ingestionJobs.map((job) => <option key={job.id} value={job.id}>{job.source_name}</option>)}</select></label><label>Response <select className={s.input} value={bankFilter.response_mode || ""} onChange={(event) => setBankFilter((current) => ({ ...current, response_mode: event.target.value || undefined }))}><option value="">All formats</option>{["select", "numeric", "matching", "subjective", "unknown"].map((mode) => <option key={mode} value={mode}>{mode}</option>)}</select></label><label>Review <select className={s.input} value={bankFilter.verification_status || ""} onChange={(event) => setBankFilter((current) => ({ ...current, verification_status: event.target.value || undefined }))}><option value="">All states</option><option value="pending_review">Pending review</option><option value="approved">Approved</option><option value="rejected">Rejected</option></select></label><label>Paper <input className={s.input} value={bankFilter.paper || ""} onChange={(event) => setBankFilter((current) => ({ ...current, paper: event.target.value || undefined }))} /></label><label>Section <input className={s.input} value={bankFilter.section || ""} onChange={(event) => setBankFilter((current) => ({ ...current, section: event.target.value || undefined }))} /></label><Button onClick={() => openBranch({})}>Browse matches</Button></div></section>
      <section className={s.bankSummary}><div><span className={s.eyebrow}>Current collection</span><h2>All classified questions</h2><p>{collectionTotal} seed questions · open any branch to read questions without leaving the taxonomy.</p></div><div className={s.chipRow}>{QUESTION_TYPES.map(([type, label]) => <span className={s.chip} key={type}>{label}: {availability(catalog.filter((row) => row.question_type === type))}</span>)}{DIFFICULTIES.map(([key, label, representative]) => <span className={s.chip} key={key}>{label}: {availability(catalog.filter((row) => representative === 1 ? row.difficulty <= 2 : representative === 3 ? row.difficulty === 3 : row.difficulty >= 4))}</span>)}</div></section>
      <section className={s.taxonomyTree} aria-label="Question taxonomy">
        <div className={s.taxonomyIntro}><div><span className={s.eyebrow}>Explore</span><h2>Browse by taxonomy</h2></div><p>Expand a branch to find its topics and questions.</p></div>
        {branches.map((exam, examIndex) => <details className={s.taxonomyExam} key={exam.exam} open={examIndex === 0}>
          <summary className={s.taxonomyHeading}><span>{exam.exam}</span><strong>{exam.count}</strong></summary>
          <div className={s.taxonomyInner}><Button size="small" onClick={() => openBranch({ exam: exam.exam })}>Browse all {exam.exam} questions</Button>
            {exam.subjects.map((subject) => <details className={s.taxonomySubject} key={subject.subject}>
              <summary className={s.taxonomyHeading}><span>{subject.subject}</span><strong>{subject.count}</strong></summary>
              <div className={s.taxonomyInner}><Button size="small" onClick={() => openBranch({ exam: exam.exam, subject: subject.subject })}>Browse {subject.subject}</Button>
                {subject.chapters.map((chapter) => <details className={s.taxonomyChapter} key={chapter.chapter}>
                  <summary className={s.taxonomyHeading}><span>{chapter.chapter || "Unclassified chapter"}</span><strong>{chapter.count}</strong></summary>
                  <div className={s.taxonomyInner}><Button size="small" onClick={() => openBranch({ exam: exam.exam, subject: subject.subject, chapter: chapter.chapter })}>Browse chapter</Button>
                    {chapter.topics.map((topic) => <div className={s.taxonomyTopic} key={topic.topic}>
                      <button className={s.taxonomyHeading} onClick={() => openBranch({ exam: exam.exam, subject: subject.subject, chapter: chapter.chapter, topic: topic.topic })}><span>{topic.topic || "Unclassified topic"}</span><strong>{topic.count}</strong></button>
                      {topic.topicOnlyCount > 0 && <button className={s.taxonomyLeaf} onClick={() => openBranch({ exam: exam.exam, subject: subject.subject, chapter: chapter.chapter, topic: topic.topic })}><span>Questions filed directly in this topic</span><strong>{topic.topicOnlyCount}</strong></button>}
                      <div className={s.taxonomyLeaves}>{topic.subtopics.map((subtopic) => <button className={s.taxonomyLeaf} key={subtopic.subtopic} onClick={() => openBranch({ exam: exam.exam, subject: subject.subject, chapter: chapter.chapter, topic: topic.topic, subtopic: subtopic.subtopic })}><span>{subtopic.subtopic}</span><strong>{subtopic.count}</strong></button>)}</div>
                    </div>)}
                  </div>
                </details>)}
              </div>
            </details>)}
          </div>
        </details>)}
      </section>
    </> : <section className={s.panel}><EmptyState title="No classified seed questions yet" description="Upload a PDF or DOCX, or paste question text. The classifier will create reviewable taxonomy-labeled seed questions." action={<Link className={`${s.button} ${s.button_primary}`} href="/ingestion">Ingest questions</Link>} /></section>}
    </> : <>
      <PageHeader eyebrow="Ingestion" title="Source ingestion" description="Upload question sets, follow processing, and review the questions created from each source." actions={<Button tone="primary" icon="plus" onClick={() => setIngestionOpen(true)}>Ingest questions</Button>} />
      <div className={s.ingestionOverview}>
        <div><span>Sources</span><strong>{ingestionJobs.length}</strong></div>
        <div><span>In progress</span><strong>{activeJobs.length}</strong></div>
        <div><span>Needs review</span><strong>{reviewCount}</strong></div>
      </div>
      {workspaceError && !ingestionJobs.length ? <section className={s.sourcePanel}><EmptyState title="Couldn’t load sources" description={workspaceError} action={<Button onClick={() => void refresh()}>Retry</Button>} /></section> : ingestionJobs.length ? <section className={s.sourcePanel} aria-labelledby="source-history-heading">
        <div className={s.sourcePanelHead}><div><span className={s.eyebrow}>Ingestion activity</span><h2 id="source-history-heading">Source history</h2><p>Processing progress and review tools for every uploaded source.</p></div></div>
        <div className={s.sourceList}>{ingestionJobs.map((job) => <article key={job.id} className={s.sourceRow}>
          <div className={s.sourceMain}><div className={s.sourceName}><strong title={job.source_name}>{job.source_name}</strong><span className={`${s.sourceStatus} ${job.state === "failed" ? s.sourceStatusFailed : ["queued", "running"].includes(job.state) ? s.sourceStatusActive : job.state === "succeeded" ? s.sourceStatusSuccess : ""}`}>{job.state}</span></div><p>{job.accepted_questions || 0} accepted <span aria-hidden="true">·</span> {job.review_questions || 0} need review</p>{job.error_message && <p className={s.errorText} role="alert">{job.error_message}</p>}</div>
          <div className={s.sourceActions}><Link className={`${s.button} ${s.button_default}`} href={`/ingestion/${job.id}`}>Review source</Link>{!["queued", "running"].includes(job.state) && <Button tone="quiet" onClick={() => void removeSource(job.id)}>Delete</Button>}</div>
          {["queued", "running"].includes(job.state) && <div className={s.sourceProgress}><IngestionProgress job={job} onPause={job.control_state !== "paused" ? () => void controlIngestion(job, "pause") : undefined} onResume={job.control_state === "paused" ? () => void controlIngestion(job, "resume") : undefined} onCancel={() => void controlIngestion(job, "cancel")} /></div>}
        </article>)}</div>
      </section> : <section className={s.sourcePanel}><EmptyState title="No sources ingested yet" description="Upload a PDF or DOCX, or paste question text to build your classified question library." action={<Button tone="primary" onClick={() => setIngestionOpen(true)}>Ingest questions</Button>} /></section>}
    </>}

    <QuestionBrowserDialog open={browserOpen} selection={selection} title="Questions in this branch" onClose={() => setBrowserOpen(false)} />
    <Dialog open={ingestionOpen} title="Ingest questions" subtitle="Classify a PDF, DOCX, or pasted question set into the seed library." onClose={() => setIngestionOpen(false)}><div className={s.modalBody}><form className={s.formGrid} onSubmit={ingest}><Field label="PDF or DOCX source (up to 35 MB)"><input className={s.input} name="file" type="file" accept="application/pdf,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx" /></Field><Field label="Or paste question text"><Textarea name="source_text" placeholder="Paste one question or a complete question set" /></Field><Field label="Additional classification instruction (optional)"><Textarea name="conversion_note" placeholder="e.g. Treat this as JEE Mathematics, Class 12." /></Field><Field label="Answer key (optional)"><Textarea name="answer_key_text" placeholder="1. B" /></Field><Field label="Answer key file"><input className={s.input} name="answer_key_file" type="file" accept=".txt,.pdf,.docx" /></Field><Field label="Solutions (optional)"><Textarea name="solutions_text" placeholder="1. Worked solution…" /></Field><Field label="Solutions file"><input className={s.input} name="solutions_file" type="file" accept=".txt,.pdf,.docx" /></Field><p className={s.muted}>The source is treated as reference content, not as instructions. Missing answers are not invented.</p><div className={s.modalActions}><Button type="button" onClick={() => setIngestionOpen(false)}>Cancel</Button><Button tone="primary" type="submit" disabled={submitting}>{submitting ? "Submitting…" : "Classify & ingest"}</Button></div></form></div></Dialog>
  </section>;
}
