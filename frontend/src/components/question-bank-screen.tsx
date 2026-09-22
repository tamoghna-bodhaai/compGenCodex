"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { availability, DIFFICULTIES, QUESTION_TYPES } from "@/lib/logic";
import type { BankSelection, CatalogRow } from "@/lib/types";
import { useWorkspace } from "@/context/workspace-context";
import { Dialog } from "@/components/dialog";
import { QuestionBrowserDialog } from "@/components/question-browser-dialog";
import { Button, EmptyState, Field, IngestionProgress, PageHeader, Textarea } from "@/components/ui";
import s from "@/styles/ui.module.css";

function unique<T>(items: T[], key: (item: T) => string) { return [...new Map(items.map((item) => [key(item), item])).values()]; }

export function QuestionBankScreen() {
  const { ingestionJobs, setIngestionJob, toast } = useWorkspace();
  const [catalog, setCatalog] = useState<CatalogRow[]>([]);
  const [selection, setSelection] = useState<BankSelection>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [browserOpen, setBrowserOpen] = useState(false);
  const [ingestionOpen, setIngestionOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const catalogRequest = useRef<AbortController | null>(null);

  const loadCatalog = useCallback(async () => { catalogRequest.current?.abort(); const controller = new AbortController(); catalogRequest.current = controller; try { setCatalog((await api.catalog(controller.signal)).items); setError(""); } catch (caught) { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Couldn’t load the catalog."); } finally { if (catalogRequest.current === controller) { catalogRequest.current = null; setLoading(false); } } }, []);
  useEffect(() => { void loadCatalog(); return () => catalogRequest.current?.abort(); }, [loadCatalog]);

  const openBranch = (next: BankSelection) => { setSelection(next); setBrowserOpen(true); };
  const latestIngestionJob = ingestionJobs[0] || null;
  useEffect(() => { if (latestIngestionJob?.state === "succeeded") void loadCatalog(); }, [latestIngestionJob?.id, latestIngestionJob?.state, loadCatalog]);

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
    try {
      const result = await api.ingest(values);
      setIngestionJob(result.job);
      setIngestionOpen(false);
      toast("Ingestion accepted. You can follow its live progress below or on the dashboard.");
    }
    catch (caught) { toast(caught instanceof Error ? caught.message : "Ingestion failed.", "error"); }
    finally { setSubmitting(false); }
  }
  async function controlIngestion(action: "pause" | "resume" | "cancel") {
    if (!latestIngestionJob) return;
    try {
      if (action === "cancel" && !window.confirm("Cancel this ingestion? Accepted questions and the report will be retained.")) return;
      const result = action === "pause" ? await api.pauseIngestion(latestIngestionJob.id) : action === "resume" ? await api.resumeIngestion(latestIngestionJob.id) : await api.cancelIngestion(latestIngestionJob.id);
      setIngestionJob(result.job);
      toast(action === "pause" ? "Ingestion paused." : action === "resume" ? "Ingestion resumed." : "Ingestion cancelled; completed work was retained.");
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t update ingestion.", "error"); }
  }

  if (loading) return <section className={s.content}><EmptyState title="Loading the question bank" description="Reading the classified seed catalog…" /></section>;
  return <section className={`${s.content} ${s.bankContent}`}>
    <PageHeader eyebrow="Question bank" title="Classified seed library" description="Open a taxonomy branch to inspect its questions, labels, and source metadata." actions={<><Link className={`${s.button} ${s.button_default}`} href="/new-paper">Create paper</Link><Button tone="primary" icon="plus" onClick={() => setIngestionOpen(true)}>Ingest questions</Button></>} />
    {latestIngestionJob && <section className={s.panel} aria-labelledby="ingestion-activity-heading"><div className={s.sectionHead}><div><span className={s.eyebrow}>Ingestion activity</span><h2 id="ingestion-activity-heading">Latest import</h2></div>{["queued", "running"].includes(latestIngestionJob.state) && <span className={s.autoUpdate}><span className={`${s.liveDot} ${s.pulse}`} />Updates automatically</span>}</div><IngestionProgress job={latestIngestionJob} onPause={latestIngestionJob.control_state !== "paused" ? () => void controlIngestion("pause") : undefined} onResume={latestIngestionJob.control_state === "paused" ? () => void controlIngestion("resume") : undefined} onCancel={() => void controlIngestion("cancel")} /></section>}
    {error ? <EmptyState title="Couldn’t load the question bank" description={error} action={<Button onClick={() => void loadCatalog()}>Retry</Button>} /> : collectionTotal ? <>
      <section className={s.bankSummary}><div><span className={s.eyebrow}>Current collection</span><h2>All classified questions</h2><p>{collectionTotal} seed questions · open any branch to read questions without leaving the taxonomy.</p></div><div className={s.chipRow}>{QUESTION_TYPES.map(([type, label]) => <span className={s.chip} key={type}>{label}: {availability(catalog.filter((row) => row.question_type === type))}</span>)}{DIFFICULTIES.map(([key, label, representative]) => <span className={s.chip} key={key}>{label}: {availability(catalog.filter((row) => representative === 1 ? row.difficulty <= 2 : representative === 3 ? row.difficulty === 3 : row.difficulty >= 4))}</span>)}</div></section>
      <section className={s.taxonomyTree} aria-label="Question taxonomy">{branches.map((exam) => <article className={s.taxonomyExam} key={exam.exam}><button className={s.taxonomyHeading} onClick={() => openBranch({ exam: exam.exam })}><span>{exam.exam}</span><strong>{exam.count}</strong></button><div>{exam.subjects.map((subject) => <section className={s.taxonomySubject} key={subject.subject}><button className={s.taxonomyHeading} onClick={() => openBranch({ exam: exam.exam, subject: subject.subject })}><span>{subject.subject}</span><strong>{subject.count}</strong></button><div>{subject.chapters.map((chapter) => <div className={s.taxonomyChapter} key={chapter.chapter}><button className={s.taxonomyHeading} onClick={() => openBranch({ exam: exam.exam, subject: subject.subject, chapter: chapter.chapter })}><span>{chapter.chapter || "Unclassified chapter"}</span><strong>{chapter.count}</strong></button><div className={s.taxonomyTopics}>{chapter.topics.map((topic) => <section className={s.taxonomyTopic} key={topic.topic}><button className={s.taxonomyHeading} onClick={() => openBranch({ exam: exam.exam, subject: subject.subject, chapter: chapter.chapter, topic: topic.topic })}><span>{topic.topic || "Unclassified topic"}</span><strong>{topic.count}</strong></button>{topic.topicOnlyCount > 0 && <button className={s.taxonomyLeaf} onClick={() => openBranch({ exam: exam.exam, subject: subject.subject, chapter: chapter.chapter, topic: topic.topic })}><span>Questions filed directly in this topic</span><strong>{topic.topicOnlyCount}</strong></button>}<div className={s.taxonomyLeaves}>{topic.subtopics.map((subtopic) => <button className={s.taxonomyLeaf} key={subtopic.subtopic} onClick={() => openBranch({ exam: exam.exam, subject: subject.subject, chapter: chapter.chapter, topic: topic.topic, subtopic: subtopic.subtopic })}><span>{subtopic.subtopic}</span><strong>{subtopic.count}</strong></button>)}</div></section>)}</div></div>)}</div></section>)}</div></article>)}</section>
    </> : <section className={s.panel}><EmptyState title="No classified seed questions yet" description="Upload a PDF or DOCX, or paste question text. The classifier will create reviewable taxonomy-labeled seed questions." action={<Button tone="primary" onClick={() => setIngestionOpen(true)}>Ingest questions</Button>} /></section>}

    <QuestionBrowserDialog open={browserOpen} selection={selection} title="Questions in this branch" onClose={() => setBrowserOpen(false)} />
    <Dialog open={ingestionOpen} title="Ingest questions" subtitle="Classify a PDF, DOCX, or pasted question set into the seed library." onClose={() => setIngestionOpen(false)}><div className={s.modalBody}><form className={s.formGrid} onSubmit={ingest}><Field label="PDF or DOCX source (up to 35 MB)"><input className={s.input} name="file" type="file" accept="application/pdf,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx" /></Field><Field label="Or paste question text"><Textarea name="source_text" placeholder="Paste one question or a complete question set" /></Field><Field label="Additional classification instruction (optional)"><Textarea name="conversion_note" placeholder="e.g. Treat this as JEE Mathematics, Class 12." /></Field><p className={s.muted}>The source is treated as reference content, not as instructions. Missing answers are not invented.</p><div className={s.modalActions}><Button type="button" onClick={() => setIngestionOpen(false)}>Cancel</Button><Button tone="primary" type="submit" disabled={submitting}>{submitting ? "Submitting…" : "Classify & ingest"}</Button></div></form></div></Dialog>
  </section>;
}
