"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { BankSolutionJob, IngestionReport, SeedQuestion } from "@/lib/types";
import { useWorkspace } from "@/context/workspace-context";
import { MathContent } from "@/components/math-content";
import { Button, EmptyState, Field, PageHeader, Textarea } from "@/components/ui";
import s from "@/styles/ui.module.css";

function hasAnswer(answer: unknown): boolean {
  if (typeof answer === "string") return !!answer.trim();
  if (Array.isArray(answer)) return answer.length > 0;
  if (answer && typeof answer === "object") return Object.keys(answer).length > 0;
  return answer != null;
}

function Question({ item, label, generation, remove }: { item: SeedQuestion; label: string; generation?: BankSolutionJob["items"][number]; remove: () => void }) {
  const solutionStatus = generation?.state === "failed" ? "Generation failed" : generation?.state === "running" ? "Generating…" : generation?.state === "queued" ? "Waiting in queue…" : "Not available";
  return <article className={s.sourceReviewQuestion}>
    <header><strong>{label}</strong><span>{[item.chapter, item.topic, item.subtopic].filter(Boolean).join(" / ")}</span></header>
    <div className={s.sourceReviewQuestionBody}>
      <div className={s.sourceReviewPrompt}>
        {item.question_group?.shared_stem && <MathContent as="div" value={item.question_group.shared_stem} />}
        <MathContent as="div" className={s.detailStem} value={item.question_json.stem} />
        {item.diagrams?.filter((d) => d.option_index == null).map((d) => d.url ? <img key={d.id} className={s.questionDiagram} src={d.url} alt={d.description} /> : <p key={d.id}>Diagram unavailable: {d.validation_notes || d.validation_status}</p>)}
        {!!item.question_json.options?.length && <ol className={s.sourceReviewOptions} type="A">{item.question_json.options.map((option, index) => <li key={index}><MathContent as="div" value={option} />{item.diagrams?.filter((d) => d.option_index === index && d.url).map((d) => <img key={d.id} className={s.questionDiagram} src={d.url!} alt={d.description} />)}</li>)}</ol>}
        <div className={s.chipRow}><span className={s.chip}>Format: {item.response_spec?.mode || item.question_type}</span><span className={s.chip}>Review: {item.verification_status.replaceAll("_", " ")}</span></div>
      </div>
      <section className={s.sourceReviewSolution} aria-label={`Solution for ${label}`}>
        {hasAnswer(item.answer_json?.correct_answer) ? <p><strong>Answer: </strong><MathContent value={typeof item.answer_json?.correct_answer === "object" ? JSON.stringify(item.answer_json.correct_answer) : String(item.answer_json?.correct_answer)} /></p> : <p><strong>Answer: </strong>Not recorded</p>}
        <details className={s.sourceReviewSolutionDetails}>
          <summary><span>Solution</span>{!item.solution && <small>{solutionStatus}</small>}</summary>
          {item.solution ? <MathContent as="div" value={item.solution} /> : generation?.state === "failed" ? <p className={s.sourceReviewFailure}>{generation.error_message || "Generation failed for this question."}</p> : <p className={s.muted}>No solution yet.</p>}
        </details>
      </section>
    </div>
    <Button size="small" tone="danger" onClick={remove}>Delete question</Button>
  </article>;
}

export function SourceReviewScreen({ jobId }: { jobId: string }) {
  const { toast, refresh } = useWorkspace();
  const [report, setReport] = useState<IngestionReport | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState({ chapter: "", topic: "", subtopic: "" });
  const [solutionJob, setSolutionJob] = useState<BankSolutionJob | null>(null);
  const [progressError, setProgressError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const load = useCallback(async () => { try { setReport(await api.ingestionReport(jobId)); setError(""); } catch (caught) { setError(caught instanceof Error ? caught.message : "Couldn’t load source review."); } }, [jobId]);
  useEffect(() => {
    let stopped = false;
    const initialize = async () => {
      let job: BankSolutionJob | null = null;
      try { job = (await api.sourceSolutionJob(jobId)).job; }
      catch { if (!stopped) setProgressError("Couldn’t load solution progress."); }
      try {
        const nextReport = await api.ingestionReport(jobId);
        if (!stopped) { setReport(nextReport); setError(""); }
      } catch (caught) {
        if (!stopped) setError(caught instanceof Error ? caught.message : "Couldn’t load source review.");
      }
      if (!stopped) setSolutionJob(job);
    };
    void initialize();
    return () => { stopped = true; };
  }, [jobId]);
  const activeJobId = solutionJob?.id;
  const activeJobState = solutionJob?.state;
  useEffect(() => {
    if (!activeJobId || (activeJobState !== "queued" && activeJobState !== "running")) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        // Read the report after the job state: a terminal state guarantees the
        // worker has already saved the answers included in the next report.
        const job = await api.bankSolutionJob(activeJobId);
        const nextReport = await api.ingestionReport(jobId);
        if (stopped) return;
        setReport(nextReport);
        setError("");
        setSolutionJob(job);
        setProgressError("");
        if (job.state === "succeeded" || job.state === "failed") {
          toast(job.state === "succeeded" ? `Generated ${job.items.filter((item) => item.state === "succeeded").length} solutions.` : "Solution generation finished with errors. See affected questions below.", job.state === "failed" ? "error" : undefined);
          return;
        }
      } catch (caught) {
        if (stopped) return;
        setProgressError(caught instanceof Error ? caught.message : "Couldn’t refresh solution progress.");
      }
      timer = setTimeout(() => void poll(), 2500);
    };
    timer = setTimeout(() => void poll(), 2500);
    return () => { stopped = true; clearTimeout(timer); };
  }, [activeJobId, activeJobState, jobId, toast]);
  const questions = useMemo(() => report?.questions.filter((q) => (!filter.chapter || q.chapter === filter.chapter) && (!filter.topic || q.topic === filter.topic) && (!filter.subtopic || q.subtopic === filter.subtopic)) || [], [report, filter]);
  const missingSolutions = questions.filter((question) => !question.solution).length;
  const missingAnswers = report?.questions.filter((question) => !hasAnswer(question.answer_json?.correct_answer)) || [];
  const questionLabels = useMemo(() => {
    const all = report?.questions || [];
    const counts = new Map<number, number>();
    for (const question of all) if (question.source_question_number != null) counts.set(question.source_question_number, (counts.get(question.source_question_number) || 0) + 1);
    return new Map(all.map((question, index) => {
      const number = question.source_question_number;
      const page = question.source_reference?.match(/\bpage\s+(\d+)/i)?.[1];
      const duplicate = number != null && (counts.get(number) || 0) > 1;
      return [question.id, number == null ? `Item ${index + 1}` : `Q${number}${duplicate ? ` (${page ? `page ${page}, ` : ""}item ${index + 1})` : ""}`];
    }));
  }, [report]);
  const eligibleMissingSolutions = questions.filter((question) => !question.solution && question.response_spec?.supported_for_generation !== false).length;
  const generationByQuestion = useMemo(() => new Map(solutionJob?.items.map((item) => [item.question_id, item]) || []), [solutionJob]);
  const jobActive = solutionJob?.state === "queued" || solutionJob?.state === "running";
  async function generateSolutions() {
    setSubmitting(true);
    try {
      const { job } = await api.generateBankSolutions({ source_job_id: jobId, ...Object.fromEntries(Object.entries(filter).filter(([, value]) => value)) });
      setSolutionJob(job);
      setProgressError("");
      if (job.state === "succeeded" || job.state === "failed") await load();
      toast(job.total_questions ? `Queued ${job.total_questions} missing solutions.` : "No eligible questions are missing solutions.");
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t queue solutions.", "error"); }
    finally { setSubmitting(false); }
  }
  async function attach(event: React.FormEvent<HTMLFormElement>) { event.preventDefault(); const form = event.currentTarget; try { const result = await api.attachSource(jobId, new FormData(form)); await load(); toast(`Mapped ${result.mappings.filter((m) => m.status === "matched").length} entries.`); form.reset(); } catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t attach material.", "error"); } }
  return <section className={`${s.content} ${s.sourceReviewContent}`}>
    <PageHeader eyebrow="Source review" title={report?.job.source_name || "Review source"} description="Trace every question, option, recorded answer, and solution." actions={<Link className={`${s.button} ${s.button_default}`} href="/ingestion">Back to sources</Link>} />
    {error ? <EmptyState title="Couldn’t load source review" description={error} action={<Button onClick={() => void load()}>Retry</Button>} /> : !report ? <EmptyState title="Loading source review" description="Reading source questions…" /> : <div className={s.sourceReviewLayout}>
      <section className={s.sourceReviewTools}>
        <p>{report.summary.accepted} accepted · {report.summary.review_needed} review needed · {report.summary.diagrams} diagrams · {report.chunks.length} extraction chunks</p>
        <p aria-live="polite">{missingAnswers.length === 0 ? `All ${report.questions.length} questions have recorded answers.` : `${missingAnswers.length} of ${report.questions.length} questions missing recorded answers: ${missingAnswers.map((question) => questionLabels.get(question.id)).join(", ")}. Map answers below, or choose to publish without them.`}</p>
        <div className={s.chipRow}>{Object.entries(report.formats).map(([key, count]) => <span className={s.chip} key={key}>{key}: {count}</span>)}</div>
        <details><summary>Taxonomy and groups</summary>{Object.entries(report.taxonomy).map(([key, count]) => <p key={key}>{key}: {count}</p>)}{Object.entries(report.groups).map(([key, count]) => <p key={key}>Group {key}: {count}</p>)}</details>
        <div className={s.sourceReviewFilters}>{(["chapter", "topic", "subtopic"] as const).map((key) => <label key={key}>{key[0].toUpperCase() + key.slice(1)}<select className={s.input} value={filter[key]} onChange={(event) => setFilter((current) => ({ ...current, [key]: event.target.value, ...(key === "chapter" ? { topic: "", subtopic: "" } : key === "topic" ? { subtopic: "" } : {}) }))}><option value="">All</option>{[...new Set(report.questions.filter((q) => (key === "chapter" || !filter.chapter || q.chapter === filter.chapter) && (key !== "subtopic" || !filter.topic || q.topic === filter.topic)).map((q) => q[key]).filter(Boolean))].map((value) => <option key={value} value={value!}>{value}</option>)}</select></label>)}</div>
        <div className={s.chipRow}><Button size="small" disabled={jobActive || submitting || eligibleMissingSolutions === 0} onClick={() => void generateSolutions()}>{jobActive ? "Generating solutions…" : submitting ? "Queuing…" : missingSolutions === 0 ? "All solutions available" : "Generate missing solutions"}</Button><a className={`${s.button} ${s.button_default}`} href={`/api/questions/ingestion-jobs/${jobId}/export?format=docx&variant=question_paper`}>Export questions</a><a className={`${s.button} ${s.button_default}`} href={`/api/questions/ingestion-jobs/${jobId}/export?format=docx&variant=answer_key`}>Export answer key</a></div>
        <p className={s.sourceReviewMissingCount} aria-live="polite">{missingSolutions} of {questions.length} {questions.length === 1 ? "question" : "questions"} missing a solution{eligibleMissingSolutions < missingSolutions ? ` · ${eligibleMissingSolutions} eligible for generation` : ""}</p>
        {solutionJob && solutionJob.total_questions > 0 && <div className={s.sourceReviewProgress} role="status" aria-live="polite"><div><strong>{jobActive ? "Generating solutions" : solutionJob.state === "failed" ? "Generation finished with errors" : "Solutions generated"}</strong><span>{solutionJob.completed_questions} of {solutionJob.total_questions} processed · {solutionJob.items.filter((item) => item.state === "succeeded").length} ready</span></div><progress max={solutionJob.total_questions} value={solutionJob.completed_questions} aria-label="Solution generation progress" /></div>}
        {progressError && <p className={s.sourceReviewFailure} role="alert">{progressError}</p>}
        <details><summary>Map answer key or solutions</summary><form className={s.formGrid} onSubmit={(event) => void attach(event)}><Field label="Source material"><select className={s.input} name="kind"><option value="answer_key">Answer key</option><option value="solutions">Solutions</option></select></Field><Field label="Paste numbered entries"><Textarea name="text" placeholder="1. B&#10;2. 42" /></Field><Field label="Or upload TXT, PDF, DOCX"><input className={s.input} name="file" type="file" accept=".txt,.pdf,.docx" /></Field><Button type="submit">Map source material</Button></form>{!!report.attachments.length && <p>Attached: {report.attachments.map((a) => `${a.kind} (${a.filename})`).join(", ")}</p>}</details>
        {report.mappings.some((m) => m.status !== "matched") && <details open><summary>Unmatched or ambiguous answer entries</summary>{report.mappings.filter((m) => m.status !== "matched").map((m) => <form key={m.id} className={s.chipRow} onSubmit={(event) => { event.preventDefault(); const id = String(new FormData(event.currentTarget).get("question_id") || ""); void api.resolveSourceMapping(jobId, m.id, id).then(async () => { await load(); toast("Answer mapped."); }).catch((caught) => toast(caught instanceof Error ? caught.message : "Couldn’t map answer.", "error")); }}><span>Q{m.source_question_number}: {m.status} — {m.note}</span><select className={s.input} name="question_id" aria-label={`Map answer ${m.source_question_number} to question`}>{report.questions.map((q) => <option key={q.id} value={q.id}>Q{q.source_question_number}: {q.question_json.stem.slice(0, 45)}</option>)}</select><Button size="small" type="submit">Map</Button></form>)}</details>}
        {report.candidates.filter((c) => c.status === "review").map((c) => <details key={c.id}><summary>Q{c.source_question_number}: {c.validation_notes}</summary><form className={s.formGrid} onSubmit={(event) => { event.preventDefault(); let changes: Record<string, unknown>; try { changes = JSON.parse(String(new FormData(event.currentTarget).get("changes") || "{}")); } catch { toast("Review edits must be valid JSON.", "error"); return; } void api.promoteCandidate(jobId, c.id, changes).then(async () => { await load(); await refresh(); toast("Question promoted."); }).catch((caught) => toast(caught instanceof Error ? caught.message : "Couldn’t promote question.", "error")); }}><Field label="Correct transcription or response rules (JSON fields)"><Textarea name="changes" defaultValue={JSON.stringify({ stem: c.payload.stem, response_spec: c.payload.response_spec, format_uncertain: c.payload.format_uncertain }, null, 2)} /></Field><Button type="submit" size="small">Accept after review</Button></form></details>)}
      </section>
      <section className={s.sourceReviewQuestions} aria-label="Source questions"><h2>Questions ({questions.length})</h2>{questions.length ? questions.map((q) => <Question key={q.id} item={q} label={questionLabels.get(q.id) || "Question"} generation={generationByQuestion.get(q.id)} remove={() => { if (!window.confirm("Delete this question?")) return; void api.deleteQuestion(q.id).then(async () => { await load(); await refresh(); toast("Question deleted."); }).catch((caught) => toast(caught instanceof Error ? caught.message : "Couldn’t delete question.", "error")); }} />) : <EmptyState title="No questions match these filters" description="Choose another chapter, topic, or subtopic." />}</section>
    </div>}
  </section>;
}
