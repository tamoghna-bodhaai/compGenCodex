"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { availability, catalogRows, DIFFICULTIES, parentBankSelection, QUESTION_TYPES } from "@/lib/logic";
import type { BankSelection, CatalogRow, SeedQuestion } from "@/lib/types";
import { useWorkspace } from "@/context/workspace-context";
import { Dialog } from "@/components/dialog";
import { MathContent } from "@/components/math-content";
import { Button, EmptyState, Field, IngestionProgress, PageHeader, Textarea } from "@/components/ui";
import s from "@/styles/ui.module.css";

function unique<T>(items: T[], key: (item: T) => string) { return [...new Map(items.map((item) => [key(item), item])).values()]; }

export function QuestionBankScreen() {
  const { ingestionJobs, setIngestionJob, toast } = useWorkspace();
  const [catalog, setCatalog] = useState<CatalogRow[]>([]);
  const [selection, setSelection] = useState<BankSelection>({});
  const [questions, setQuestions] = useState<SeedQuestion[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<SeedQuestion | null>(null);
  const [ingestionOpen, setIngestionOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const catalogRequest = useRef<AbortController | null>(null);
  const questionListRequest = useRef<AbortController | null>(null);
  const detailRequest = useRef<AbortController | null>(null);

  const loadCatalog = useCallback(async () => { catalogRequest.current?.abort(); const controller = new AbortController(); catalogRequest.current = controller; try { setCatalog((await api.catalog(controller.signal)).items); setError(""); } catch (caught) { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Couldn’t load the catalog."); } finally { if (catalogRequest.current === controller) { catalogRequest.current = null; setLoading(false); } } }, []);
  useEffect(() => { void loadCatalog(); return () => { catalogRequest.current?.abort(); questionListRequest.current?.abort(); detailRequest.current?.abort(); }; }, [loadCatalog]);

  const loadQuestions = useCallback(async (nextSelection: BankSelection, nextOffset = 0) => {
    questionListRequest.current?.abort();
    if (!Object.keys(nextSelection).length) { setQuestions([]); setTotal(0); setOffset(0); return; }
    const controller = new AbortController(); questionListRequest.current = controller;
    const query = new URLSearchParams({ limit: "50", offset: String(nextOffset) });
    Object.entries(nextSelection).forEach(([key, value]) => { if (value) query.set(key, value); });
    try { const result = await api.questions(query, controller.signal); if (!controller.signal.aborted) { setQuestions(result.items); setTotal(result.total); setOffset(result.offset); } }
    catch (caught) { if (!controller.signal.aborted) toast(caught instanceof Error ? caught.message : "Couldn’t load questions.", "error"); }
    finally { if (questionListRequest.current === controller) questionListRequest.current = null; }
  }, [toast]);

  const selectBranch = (next: BankSelection) => { setSelection(next); void loadQuestions(next); };
  const latestIngestionJob = ingestionJobs[0] || null;
  useEffect(() => { if (latestIngestionJob?.state === "succeeded") void loadCatalog(); }, [latestIngestionJob?.id, latestIngestionJob?.state, loadCatalog]);

  const rows = catalogRows(catalog, Object.fromEntries(Object.entries(selection).map(([key, value]) => [key, value ? [value] : []])) as Partial<Record<keyof CatalogRow, string[]>>);
  const collectionTotal = availability(catalog);
  const selectedLabel = selection.subtopic || selection.topic || selection.chapter || selection.subject || selection.exam || "All classified questions";
  const branches = useMemo(() => {
    const examRows = unique(catalog.filter((row) => row.exam), (row) => row.exam);
    return examRows.map((examRow) => {
      const exam = examRow.exam; const withinExam = catalog.filter((row) => row.exam === exam);
      return { exam, count: availability(withinExam), subjects: unique(withinExam, (row) => row.subject).map((subjectRow) => { const subject = subjectRow.subject; const withinSubject = withinExam.filter((row) => row.subject === subject); return { subject, count: availability(withinSubject), chapters: unique(withinSubject.filter((row) => row.chapter), (row) => row.chapter || "").map((chapterRow) => { const chapter = chapterRow.chapter || ""; const withinChapter = withinSubject.filter((row) => row.chapter === chapter); return { chapter, count: availability(withinChapter), leaves: unique(withinChapter.filter((row) => row.topic), (row) => `${row.topic}::${row.subtopic}`).map((leaf) => ({ topic: leaf.topic || "", subtopic: leaf.subtopic || "", count: availability(withinChapter.filter((row) => row.topic === leaf.topic && row.subtopic === leaf.subtopic)) })) }; }) }; }) };
    });
  }, [catalog]);

  async function openQuestion(id: string) { detailRequest.current?.abort(); const controller = new AbortController(); detailRequest.current = controller; try { const result = await api.question(id, controller.signal); if (!controller.signal.aborted) setDetail(result); } catch (caught) { if (!controller.signal.aborted) toast(caught instanceof Error ? caught.message : "Couldn’t load the question.", "error"); } finally { if (detailRequest.current === controller) detailRequest.current = null; } }
  async function ingest(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (submitting) return; setSubmitting(true);
    const values = new FormData(event.currentTarget); const file = values.get("file") as File | null; const text = String(values.get("source_text") || "").trim();
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

  if (loading) return <section className={s.content}><EmptyState title="Loading the question bank" description="Reading the classified seed catalog…" /></section>;
  return <section className={`${s.content} ${s.bankContent}`}>
    <PageHeader eyebrow="Question bank" title="Classified seed library" description="Open a taxonomy branch to inspect its questions, labels, and source metadata." actions={<><Link className={`${s.button} ${s.button_default}`} href="/new-paper">Create paper</Link><Button tone="primary" icon="plus" onClick={() => setIngestionOpen(true)}>Ingest questions</Button></>} />
    {latestIngestionJob && <section className={s.panel} aria-labelledby="ingestion-activity-heading"><div className={s.sectionHead}><div><span className={s.eyebrow}>Ingestion activity</span><h2 id="ingestion-activity-heading">Latest import</h2></div>{["queued", "running"].includes(latestIngestionJob.state) && <span className={s.autoUpdate}><span className={`${s.liveDot} ${s.pulse}`} />Updates automatically</span>}</div><IngestionProgress job={latestIngestionJob} /></section>}
    {error ? <EmptyState title="Couldn’t load the question bank" description={error} action={<Button onClick={() => void loadCatalog()}>Retry</Button>} /> : collectionTotal ? <>
      <section className={s.bankSummary}><div><span className={s.eyebrow}>Current collection</span><h2>{selectedLabel}</h2><p>{availability(rows)} of {collectionTotal} seed questions · imported questions remain pending review until verified.</p></div>{Object.keys(selection).length > 0 && <div className={s.inlineActions}><Button onClick={() => selectBranch(parentBankSelection(selection))}>← Back</Button><Button tone="quiet" onClick={() => selectBranch({})}>Clear selection</Button></div>}<div className={s.chipRow}>{QUESTION_TYPES.map(([type, label]) => <span className={s.chip} key={type}>{label}: {availability(rows.filter((row) => row.question_type === type))}</span>)}{DIFFICULTIES.map(([key, label, representative]) => <span className={s.chip} key={key}>{label}: {availability(rows.filter((row) => representative === 1 ? row.difficulty <= 2 : representative === 3 ? row.difficulty === 3 : row.difficulty >= 4))}</span>)}</div></section>
      <section className={s.taxonomyTree} aria-label="Question taxonomy">{branches.map((exam) => <article className={s.taxonomyExam} key={exam.exam}><button className={s.taxonomyHeading} onClick={() => selectBranch({ exam: exam.exam })}><span>{exam.exam}</span><strong>{exam.count}</strong></button><div>{exam.subjects.map((subject) => <section className={s.taxonomySubject} key={subject.subject}><button className={s.taxonomyHeading} onClick={() => selectBranch({ exam: exam.exam, subject: subject.subject })}><span>{subject.subject}</span><strong>{subject.count}</strong></button><div>{subject.chapters.map((chapter) => <div className={s.taxonomyChapter} key={chapter.chapter}><button className={s.taxonomyHeading} onClick={() => selectBranch({ exam: exam.exam, subject: subject.subject, chapter: chapter.chapter })}><span>{chapter.chapter || "Unclassified chapter"}</span><strong>{chapter.count}</strong></button><div className={s.taxonomyLeaves}>{chapter.leaves.map((leaf) => <button className={s.taxonomyLeaf} key={`${leaf.topic}-${leaf.subtopic}`} onClick={() => selectBranch({ exam: exam.exam, subject: subject.subject, chapter: chapter.chapter, topic: leaf.topic, ...(leaf.subtopic ? { subtopic: leaf.subtopic } : {}) })}><span>{leaf.subtopic ? `${leaf.topic} › ${leaf.subtopic}` : leaf.topic}</span><strong>{leaf.count}</strong></button>)}</div></div>)}</div></section>)}</div></article>)}</section>
      {Object.keys(selection).length > 0 && <section className={s.questionListPanel}><div className={s.sectionHead}><div><h2>Questions in this selection</h2><p>{total} classified seed question{total === 1 ? "" : "s"}</p></div><span>Showing {total ? offset + 1 : 0}–{Math.min(offset + questions.length, total)}</span></div><div className={s.bankQuestionGrid}>{questions.length ? questions.map((question) => <button className={s.bankQuestionCard} key={question.id} onClick={() => void openQuestion(question.id)}><span className={s.eyebrow}>Question {question.source_reference?.match(/question (\d+)/i)?.[1] || ""}</span><MathContent as="p" value={question.question_json.stem} /><small>{[question.question_type.replaceAll("_", " "), `Difficulty ${question.difficulty}/5`, question.verification_status.replaceAll("_", " ")].join(" · ")}</small></button>) : <EmptyState title="No questions match this branch" description="Choose a different taxonomy branch." />}</div>{total > 50 && <footer className={s.pagination}><Button size="small" disabled={!offset} onClick={() => void loadQuestions(selection, Math.max(0, offset - 50))}>Previous</Button><span>Page {Math.floor(offset / 50) + 1} of {Math.ceil(total / 50)}</span><Button size="small" disabled={offset + 50 >= total} onClick={() => void loadQuestions(selection, offset + 50)}>Next</Button></footer>}</section>}
    </> : <section className={s.panel}><EmptyState title="No classified seed questions yet" description="Upload a PDF or DOCX, or paste question text. The classifier will create reviewable taxonomy-labeled seed questions." action={<Button tone="primary" onClick={() => setIngestionOpen(true)}>Ingest questions</Button>} /></section>}

    <Dialog open={!!detail} title="Seed question" subtitle={detail ? [detail.exam, detail.subject, detail.chapter].filter(Boolean).join(" · ") : ""} onClose={() => setDetail(null)}><div className={s.modalBody}>{detail && <div className={s.detailBody}><div className={s.chipRow}><span className={s.chip}>{detail.topic || "No topic"}</span>{detail.subtopic && <span className={s.chip}>{detail.subtopic}</span>}<span className={s.chip}>{detail.question_type.replaceAll("_", " ")}</span><span className={s.chip}>Difficulty {detail.difficulty}/5</span></div><MathContent as="div" className={s.detailStem} value={detail.question_json.stem} />{!!detail.question_json.options?.length && <ol className={s.optionList}>{detail.question_json.options.map((option, index) => <li key={index}><MathContent value={option} /></li>)}</ol>}<div className={s.rule} /><p>Source: {detail.source_reference || detail.source || "Not stated"}</p><p>Review state: {detail.verification_status.replaceAll("_", " ")}</p><p>{detail.answer_json?.correct_answer ? <>Recorded answer: <strong>{detail.answer_json.correct_answer}</strong></> : "No recorded answer; verify against the original source."}</p></div>}</div></Dialog>
    <Dialog open={ingestionOpen} title="Ingest questions" subtitle="Classify a PDF, DOCX, or pasted question set into the seed library." onClose={() => setIngestionOpen(false)}><div className={s.modalBody}><form className={s.formGrid} onSubmit={ingest}><Field label="PDF or DOCX source (up to 35 MB)"><input className={s.input} name="file" type="file" accept="application/pdf,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx" /></Field><Field label="Or paste question text"><Textarea name="source_text" placeholder="Paste one question or a complete question set" /></Field><Field label="Additional classification instruction (optional)"><Textarea name="conversion_note" placeholder="e.g. Treat this as JEE Mathematics, Class 12." /></Field><p className={s.muted}>The source is treated as reference content, not as instructions. Missing answers are not invented.</p><div className={s.modalActions}><Button type="button" onClick={() => setIngestionOpen(false)}>Cancel</Button><Button tone="primary" type="submit" disabled={submitting}>{submitting ? "Submitting…" : "Classify & ingest"}</Button></div></form></div></Dialog>
  </section>;
}
