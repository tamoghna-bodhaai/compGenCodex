"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { availability, DIFFICULTIES, QUESTION_TYPES } from "@/lib/logic";
import type { BankSelection, CatalogRow, IngestionJob, IngestionReport } from "@/lib/types";
import { useWorkspace } from "@/context/workspace-context";
import { Dialog } from "@/components/dialog";
import { QuestionBrowserDialog } from "@/components/question-browser-dialog";
import { Button, EmptyState, Field, IngestionProgress, PageHeader, Textarea } from "@/components/ui";
import s from "@/styles/ui.module.css";

function unique<T>(items: T[], key: (item: T) => string) { return [...new Map(items.map((item) => [key(item), item])).values()]; }

export function QuestionBankScreen({ view = "bank" }: { view?: "bank" | "ingestion" }) {
  const { ingestionJobs, loading: workspaceLoading, error: workspaceError, setIngestionJob, refresh, toast } = useWorkspace();
  const [catalog, setCatalog] = useState<CatalogRow[]>([]);
  const [selection, setSelection] = useState<BankSelection>({});
  const [bankFilter, setBankFilter] = useState<BankSelection>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [browserOpen, setBrowserOpen] = useState(false);
  const [ingestionOpen, setIngestionOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [reviewJobId, setReviewJobId] = useState<string | null>(null);
  const [reviewTaxonomy, setReviewTaxonomy] = useState<{ chapter: string; topic: string; subtopic: string }>({ chapter: "", topic: "", subtopic: "" });
  const [report, setReport] = useState<IngestionReport | null>(null);
  const [solutionJobId, setSolutionJobId] = useState<string | null>(null);
  const catalogRequest = useRef<AbortController | null>(null);

  const loadCatalog = useCallback(async () => { catalogRequest.current?.abort(); const controller = new AbortController(); catalogRequest.current = controller; try { setCatalog((await api.catalog(controller.signal)).items); setError(""); } catch (caught) { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Couldn’t load the catalog."); } finally { if (catalogRequest.current === controller) { catalogRequest.current = null; setLoading(false); } } }, []);
  useEffect(() => { void loadCatalog(); return () => catalogRequest.current?.abort(); }, [loadCatalog]);

  const openBranch = (next: BankSelection) => { setSelection({ ...bankFilter, ...next }); setBrowserOpen(true); };
  const activeJobs = ingestionJobs.filter((job) => ["queued", "running"].includes(job.state));
  const reviewCount = ingestionJobs.reduce((total, job) => total + (job.review_questions || 0), 0);
  useEffect(() => { if (!reviewJobId) { setReport(null); return; } let active = true; api.ingestionReport(reviewJobId).then((data) => { if (active) setReport(data); }).catch((caught) => toast(caught instanceof Error ? caught.message : "Couldn’t load review.", "error")); return () => { active = false; }; }, [reviewJobId, ingestionJobs, toast]);
  useEffect(() => { if (!solutionJobId) return; const timer = window.setInterval(() => { void api.bankSolutionJob(solutionJobId).then((job) => { if (["succeeded", "failed"].includes(job.state)) { window.clearInterval(timer); setSolutionJobId(null); toast(job.state === "succeeded" ? `Generated ${job.completed_questions} solutions.` : job.error_message || "Solution generation needs review.", job.state === "succeeded" ? undefined : "error"); if (reviewJobId) void api.ingestionReport(reviewJobId).then(setReport); } }); }, 2500); return () => window.clearInterval(timer); }, [solutionJobId, reviewJobId, toast]);
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
  async function attachMaterial(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!reviewJobId) return;
    try { const result = await api.attachSource(reviewJobId, new FormData(event.currentTarget)); setReport(await api.ingestionReport(reviewJobId)); toast(`Mapped ${result.mappings.filter((mapping) => mapping.status === "matched").length} entries; review unmatched items below.`); event.currentTarget.reset(); }
    catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t attach source material.", "error"); }
  }
  async function removeSource(id: string) {
    if (!window.confirm("Permanently delete this source, its questions, attachments, and diagrams? Generated papers remain saved.")) return;
    try { await api.deleteIngestionJob(id); setReviewJobId(null); await Promise.all([loadCatalog(), refresh()]); toast("Source deleted."); }
    catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t delete source.", "error"); }
  }

  if (view === "bank" && loading) return <section className={s.content}><EmptyState title="Loading the question bank" description="Reading the classified seed catalog…" /></section>;
  if (view === "ingestion" && workspaceLoading) return <section className={s.content}><EmptyState title="Loading sources" description="Checking ingestion activity…" /></section>;
  return <section className={`${s.content} ${s.bankContent}`}>
    {view === "bank" ? <>
      <PageHeader eyebrow="Question bank" title="Browse questions" description="Explore classified questions by subject and topic, or use filters to find a specific set." actions={<><Link className={`${s.button} ${s.button_default}`} href="/new-paper">Create paper</Link><Link className={`${s.button} ${s.button_primary}`} href="/ingestion">Ingest questions</Link></>} />
      {error ? <EmptyState title="Couldn’t load the question bank" description={error} action={<Button onClick={() => void loadCatalog()}>Retry</Button>} /> : collectionTotal ? <>
      <section className={s.bankFilters}><div className={s.bankFiltersHead}><div><span className={s.eyebrow}>Find questions</span><h2>Filter the library</h2></div><span>Choose filters, then open matching questions</span></div><div className={s.bankFilterGrid}><label>Source <select className={s.input} value={bankFilter.source_job_id || ""} onChange={(event) => setBankFilter((current) => ({ ...current, source_job_id: event.target.value || undefined }))}><option value="">All sources</option>{ingestionJobs.map((job) => <option key={job.id} value={job.id}>{job.source_name}</option>)}</select></label><label>Response <select className={s.input} value={bankFilter.response_mode || ""} onChange={(event) => setBankFilter((current) => ({ ...current, response_mode: event.target.value || undefined }))}><option value="">All formats</option>{["select", "numeric", "matching", "subjective", "unknown"].map((mode) => <option key={mode} value={mode}>{mode}</option>)}</select></label><label>Review <select className={s.input} value={bankFilter.verification_status || ""} onChange={(event) => setBankFilter((current) => ({ ...current, verification_status: event.target.value || undefined }))}><option value="">All states</option><option value="pending_review">Pending review</option><option value="accepted">Accepted</option></select></label><label>Paper <input className={s.input} value={bankFilter.paper || ""} onChange={(event) => setBankFilter((current) => ({ ...current, paper: event.target.value || undefined }))} /></label><label>Section <input className={s.input} value={bankFilter.section || ""} onChange={(event) => setBankFilter((current) => ({ ...current, section: event.target.value || undefined }))} /></label><Button onClick={() => openBranch({})}>Browse matches</Button></div></section>
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
          <div className={s.sourceActions}><Button onClick={() => setReviewJobId(job.id)}>Review source</Button>{!["queued", "running"].includes(job.state) && <Button tone="quiet" onClick={() => void removeSource(job.id)}>Delete</Button>}</div>
          {["queued", "running"].includes(job.state) && <div className={s.sourceProgress}><IngestionProgress job={job} onPause={job.control_state !== "paused" ? () => void controlIngestion(job, "pause") : undefined} onResume={job.control_state === "paused" ? () => void controlIngestion(job, "resume") : undefined} onCancel={() => void controlIngestion(job, "cancel")} /></div>}
        </article>)}</div>
      </section> : <section className={s.sourcePanel}><EmptyState title="No sources ingested yet" description="Upload a PDF or DOCX, or paste question text to build your classified question library." action={<Button tone="primary" onClick={() => setIngestionOpen(true)}>Ingest questions</Button>} /></section>}
    </>}

    <QuestionBrowserDialog open={browserOpen} selection={selection} title="Questions in this branch" onClose={() => setBrowserOpen(false)} />
    <Dialog open={!!reviewJobId} wide title="Source review" subtitle={report?.job.source_name || "Loading…"} onClose={() => setReviewJobId(null)}><div className={s.modalBody}>{report && <div className={s.detailBody}>
      <p>{report.summary.accepted} accepted · {report.summary.review_needed} review needed · {report.summary.diagrams} diagrams · {report.chunks.length} extraction chunks</p>
      <div className={s.chipRow}>{Object.entries(report.formats).map(([key, count]) => <span className={s.chip} key={key}>{key}: {count}</span>)}</div>
      <details><summary>Taxonomy and groups</summary>{Object.entries(report.taxonomy).map(([key, count]) => <p key={key}>{key}: {count}</p>)}{Object.entries(report.groups).map(([key, count]) => <p key={key}>Group {key}: {count}</p>)}</details>
      <div className={s.chipRow}><label>Chapter <select className={s.input} value={reviewTaxonomy.chapter} onChange={(event) => setReviewTaxonomy({ chapter: event.target.value, topic: "", subtopic: "" })}><option value="">All</option>{[...new Set(report.questions.map((question) => question.chapter).filter(Boolean))].map((value) => <option key={value} value={value!}>{value}</option>)}</select></label><label>Topic <select className={s.input} value={reviewTaxonomy.topic} onChange={(event) => setReviewTaxonomy((current) => ({ ...current, topic: event.target.value, subtopic: "" }))}><option value="">All</option>{[...new Set(report.questions.filter((question) => !reviewTaxonomy.chapter || question.chapter === reviewTaxonomy.chapter).map((question) => question.topic).filter(Boolean))].map((value) => <option key={value} value={value!}>{value}</option>)}</select></label><label>Subtopic <select className={s.input} value={reviewTaxonomy.subtopic} onChange={(event) => setReviewTaxonomy((current) => ({ ...current, subtopic: event.target.value }))}><option value="">All</option>{[...new Set(report.questions.filter((question) => (!reviewTaxonomy.chapter || question.chapter === reviewTaxonomy.chapter) && (!reviewTaxonomy.topic || question.topic === reviewTaxonomy.topic)).map((question) => question.subtopic).filter(Boolean))].map((value) => <option key={value} value={value!}>{value}</option>)}</select></label></div>
      <div className={s.chipRow}><Button size="small" onClick={() => { setSelection({ source_job_id: report.job.id, ...Object.fromEntries(Object.entries(reviewTaxonomy).filter(([, value]) => value)) }); setBrowserOpen(true); }}>Browse source questions</Button><Button size="small" disabled={!!solutionJobId} onClick={() => void api.generateBankSolutions({ source_job_id: report.job.id, ...Object.fromEntries(Object.entries(reviewTaxonomy).filter(([, value]) => value)) }).then(({ job }) => { setSolutionJobId(job.id); toast(`Queued ${job.total_questions} missing solutions.`); }).catch((caught) => toast(caught instanceof Error ? caught.message : "Couldn’t queue solutions.", "error"))}>Generate missing solutions</Button><a className={`${s.button} ${s.button_default}`} href={`/api/questions/ingestion-jobs/${report.job.id}/export?format=docx&variant=question_paper`}>Export questions</a><a className={`${s.button} ${s.button_default}`} href={`/api/questions/ingestion-jobs/${report.job.id}/export?format=docx&variant=answer_key`}>Export answer key</a></div>
      <form className={s.formGrid} onSubmit={(event) => void attachMaterial(event)}><Field label="Source material"><select className={s.input} name="kind"><option value="answer_key">Answer key</option><option value="solutions">Solutions</option></select></Field><Field label="Paste numbered entries"><Textarea name="text" placeholder="1. B&#10;2. 42" /></Field><Field label="Or upload TXT, PDF, DOCX"><input className={s.input} name="file" type="file" accept=".txt,.pdf,.docx" /></Field><Button type="submit">Map source material</Button></form>
      {!!report.attachments.length && <p>Attached: {report.attachments.map((attachment) => `${attachment.kind} (${attachment.filename})`).join(", ")}</p>}
      {report.mappings.some((mapping) => mapping.status !== "matched") && <details open><summary>Unmatched or ambiguous answer entries</summary>{report.mappings.filter((mapping) => mapping.status !== "matched").map((mapping) => <form key={mapping.id} className={s.chipRow} onSubmit={(event) => { event.preventDefault(); const questionId = String(new FormData(event.currentTarget).get("question_id") || ""); void api.resolveSourceMapping(report.job.id, mapping.id, questionId).then(async () => { setReport(await api.ingestionReport(report.job.id)); toast("Answer mapped."); }).catch((caught) => toast(caught instanceof Error ? caught.message : "Couldn’t map answer.", "error")); }}><span>Q{mapping.source_question_number}: {mapping.status} — {mapping.note}</span><select className={s.input} name="question_id" aria-label={`Map answer ${mapping.source_question_number} to question`}>{report.questions.map((question) => <option key={question.id} value={question.id}>Q{question.source_question_number}: {question.question_json.stem.slice(0, 45)}</option>)}</select><Button size="small" type="submit">Map</Button></form>)}</details>}
      {report.candidates.filter((candidate) => candidate.status === "review").map((candidate) => <details key={candidate.id}><summary>Q{candidate.source_question_number}: {candidate.validation_notes}</summary><form className={s.formGrid} onSubmit={(event) => { event.preventDefault(); const values = new FormData(event.currentTarget); let changes: Record<string, unknown>; try { changes = JSON.parse(String(values.get("changes") || "{}")) as Record<string, unknown>; } catch { toast("Review edits must be valid JSON.", "error"); return; } void api.promoteCandidate(report.job.id, candidate.id, changes).then(async () => { setReport(await api.ingestionReport(report.job.id)); await loadCatalog(); toast("Question promoted."); }).catch((caught) => toast(caught instanceof Error ? caught.message : "Couldn’t promote question.", "error")); }}><Field label="Correct transcription or response rules (JSON fields)"><Textarea name="changes" defaultValue={JSON.stringify({ stem: candidate.payload.stem, response_spec: candidate.payload.response_spec, format_uncertain: candidate.payload.format_uncertain }, null, 2)} /></Field><Button type="submit" size="small">Accept after review</Button></form></details>)}
      {report.questions.filter((question) => (!reviewTaxonomy.chapter || question.chapter === reviewTaxonomy.chapter) && (!reviewTaxonomy.topic || question.topic === reviewTaxonomy.topic) && (!reviewTaxonomy.subtopic || question.subtopic === reviewTaxonomy.subtopic)).map((question) => <article key={question.id} className={s.bankQuestionCard}><strong>Q{question.source_question_number} · {question.chapter} / {question.topic} / {question.subtopic || "—"}</strong><p>{question.question_json.stem}</p><p>Format: {question.response_spec?.mode} · Review: {question.verification_status}</p>{question.diagrams?.map((diagram) => diagram.url && <img key={diagram.id} className={s.questionDiagram} src={diagram.url} alt={diagram.description} />)}<Button size="small" onClick={() => void api.deleteQuestion(question.id).then(async () => { setReport(await api.ingestionReport(report.job.id)); await loadCatalog(); toast("Question deleted."); })}>Delete question</Button></article>)}
    </div>}</div></Dialog>
    <Dialog open={ingestionOpen} title="Ingest questions" subtitle="Classify a PDF, DOCX, or pasted question set into the seed library." onClose={() => setIngestionOpen(false)}><div className={s.modalBody}><form className={s.formGrid} onSubmit={ingest}><Field label="PDF or DOCX source (up to 35 MB)"><input className={s.input} name="file" type="file" accept="application/pdf,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx" /></Field><Field label="Or paste question text"><Textarea name="source_text" placeholder="Paste one question or a complete question set" /></Field><Field label="Additional classification instruction (optional)"><Textarea name="conversion_note" placeholder="e.g. Treat this as JEE Mathematics, Class 12." /></Field><Field label="Answer key (optional)"><Textarea name="answer_key_text" placeholder="1. B" /></Field><Field label="Answer key file"><input className={s.input} name="answer_key_file" type="file" accept=".txt,.pdf,.docx" /></Field><Field label="Solutions (optional)"><Textarea name="solutions_text" placeholder="1. Worked solution…" /></Field><Field label="Solutions file"><input className={s.input} name="solutions_file" type="file" accept=".txt,.pdf,.docx" /></Field><p className={s.muted}>The source is treated as reference content, not as instructions. Missing answers are not invented.</p><div className={s.modalActions}><Button type="button" onClick={() => setIngestionOpen(false)}>Cancel</Button><Button tone="primary" type="submit" disabled={submitting}>{submitting ? "Submitting…" : "Classify & ingest"}</Button></div></form></div></Dialog>
  </section>;
}
