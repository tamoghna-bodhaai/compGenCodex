"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { BankSelection, SeedQuestion } from "@/lib/types";
import { Dialog } from "@/components/dialog";
import { MathContent } from "@/components/math-content";
import { Button, EmptyState } from "@/components/ui";
import s from "@/styles/ui.module.css";
import { useWorkspace } from "@/context/workspace-context";

function breadcrumb(selection: BankSelection) {
  return [selection.exam, selection.subject, selection.chapter || undefined, selection.topic, selection.subtopic].filter(Boolean).join(" · ");
}

function hasRecordedAnswer(question: SeedQuestion) {
  const answer = question.answer_json?.correct_answer;
  return answer !== null && answer !== undefined && answer !== "" && (!Array.isArray(answer) || answer.length > 0);
}

function QuestionDetail({ question, onClose, onChange }: { question: SeedQuestion | null; onClose: () => void; onChange: (question: SeedQuestion) => void }) {
  const { toast, role } = useWorkspace();
  const [solving, setSolving] = useState(false);
  async function generateSolution() {
    if (!question) return;
    setSolving(true);
    try {
      const { job } = await api.generateBankSolutions({ question_ids: [question.id], replace_existing: !!question.solution });
      if (!job.total_questions) { toast("This question is not eligible for solution generation.", "error"); return; }
      for (let attempt = 0; attempt < 60; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
        const progress = await api.bankSolutionJob(job.id);
        if (progress.state === "succeeded") { onChange(await api.question(question.id)); toast("Solution generated and pending review."); return; }
        if (progress.state === "failed") throw new Error(progress.error_message || "Solution generation failed.");
      }
      toast("Solution generation is still running. Reopen this question to see the result.");
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t generate solution.", "error"); }
    finally { setSolving(false); }
  }
  return <Dialog open={!!question} title="Seed question" subtitle={question ? [question.exam, question.subject, question.chapter].filter(Boolean).join(" · ") : ""} onClose={onClose}>
    <div className={s.modalBody}>{question && <div className={s.detailBody}>
      <div className={s.chipRow}><span className={s.chip}>{question.topic || "No topic"}</span>{question.subtopic && <span className={s.chip}>{question.subtopic}</span>}<span className={s.chip}>{question.question_type.replaceAll("_", " ")}</span><span className={s.chip}>Difficulty {question.difficulty}/5</span>{question.question_group && <span className={s.chip}>Group {question.question_group.id}</span>}</div>
      <MathContent as="div" className={s.detailStem} value={question.question_json.stem} />
      {question.diagrams?.filter((diagram) => diagram.option_index == null).map((diagram) => diagram.url ? <img key={diagram.id} className={s.questionDiagram} src={diagram.url} alt={diagram.description} /> : <p key={diagram.id} className={s.muted}>Diagram unavailable: {diagram.validation_notes || diagram.validation_status}</p>)}
      {!!question.question_json.options?.length && <ol className={s.optionList}>{question.question_json.options.map((option, index) => <li key={index}><MathContent value={option} />{question.diagrams?.filter((diagram) => diagram.option_index === index && diagram.url).map((diagram) => <img key={diagram.id} className={s.questionDiagram} src={diagram.url!} alt={diagram.description} />)}</li>)}</ol>}
      {question.response_spec && <div><p>Response: {question.response_spec.mode} {question.response_spec.min_selections != null ? `· select ${question.response_spec.min_selections}–${question.response_spec.max_selections}` : ""}</p>{question.response_spec.instructions && <p>{question.response_spec.instructions}</p>}{question.response_spec.marking?.source_instructions && <p>Marking: {question.response_spec.marking.source_instructions}</p>}{question.response_spec.numeric && <p>Numeric rule: {question.response_spec.numeric.integer_only ? "integer only" : "decimal allowed"}{question.response_spec.numeric.permitted_precision != null ? `, ${question.response_spec.numeric.permitted_precision} decimal places` : ""}</p>}{question.response_spec.list_i?.length ? <p>List I: {question.response_spec.list_i.join("; ")} · List II: {question.response_spec.list_ii?.join("; ")}</p> : null}</div>}
      <div className={s.rule} /><p>Source: {question.source_reference || question.source || "Not stated"}</p><p>Review state: {question.verification_status.replaceAll("_", " ")}</p>
      <p>{question.answer_json?.correct_answer ? <>Recorded answer ({question.answer_provenance || "unspecified"}): <strong>{typeof question.answer_json.correct_answer === "string" ? question.answer_json.correct_answer : JSON.stringify(question.answer_json.correct_answer)}</strong></> : "No recorded answer; verify against the original source."}</p>{question.solution && <MathContent as="p" value={question.solution} />}
      {role === "admin" && question.response_spec?.supported_for_generation && question.solution_provenance !== "source" && <Button size="small" disabled={solving} onClick={() => void generateSolution()}>{solving ? "Generating…" : question.solution ? "Retry AI solution" : "Generate solution"}</Button>}
    </div>}</div>
  </Dialog>;
}

export function QuestionBrowserDialog({ open, selection, chapters, title = "Questions", selectedIds, unavailableIds = [], selectable = false, selectionMode = "source", onConfirm, onClose }: {
  open: boolean; selection: BankSelection; chapters?: string[]; title?: string; selectedIds?: string[]; unavailableIds?: string[]; selectable?: boolean; selectionMode?: "source" | "exact"; onConfirm?: (ids: string[]) => void; onClose: () => void;
}) {
  const [questions, setQuestions] = useState<SeedQuestion[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [chosen, setChosen] = useState<string[]>(selectedIds || []);
  const [detail, setDetail] = useState<SeedQuestion | null>(null);
  const [error, setError] = useState("");

  useEffect(() => { if (open) { setChosen(selectedIds || []); setOffset(0); } }, [open, selectedIds]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const query = new URLSearchParams({ limit: "20", offset: String(offset) });
    Object.entries(selection).forEach(([key, value]) => { if (value) query.set(key, value); });
    chapters?.forEach((chapter) => query.append("chapter", chapter));
    api.questions(query, controller.signal).then((result) => { setQuestions(result.items); setTotal(result.total); setError(""); }).catch((caught) => { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Couldn’t load questions."); });
    return () => controller.abort();
  }, [open, selection, chapters, offset]);

  function toggle(id: string) { if (unavailableIds.includes(id)) return; setChosen((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]); }
  const page = Math.floor(offset / 20) + 1;
  return <>
    <Dialog open={open} wide title={title} subtitle={breadcrumb(selection)} onClose={onClose} footer={selectable ? <div className={s.modalActions}><span className={s.muted}>{chosen.length} question{chosen.length === 1 ? "" : "s"} selected</span><Button tone="primary" onClick={() => onConfirm?.(chosen)}>Use selected questions</Button></div> : undefined}>
      <div className={s.modalBody}><div className={s.questionBrowserHead}><p>{total} classified question{total === 1 ? "" : "s"}</p>{selectable && <p>{selectionMode === "exact" ? "Selected questions are copied into the paper." : "Selected questions are reusable sources for generated variations."}</p>}</div>
        {error ? <EmptyState title="Couldn’t load questions" description={error} /> : questions.length ? <div className={s.bankQuestionGrid}>{questions.map((question) => <article className={s.bankQuestionCard} key={question.id}>
          <div className={s.questionBrowserCardHead}><span className={s.eyebrow}>Question {question.source_reference?.match(/question (\d+)/i)?.[1] || ""}</span>{selectable && <label className={s.seedToggle}><input type="checkbox" checked={chosen.includes(question.id)} disabled={unavailableIds.includes(question.id) || !hasRecordedAnswer(question) || (selectionMode === "source" && !question.response_spec?.supported_for_generation)} onChange={() => toggle(question.id)} /> {unavailableIds.includes(question.id) ? "Already selected in another section" : !hasRecordedAnswer(question) ? "Missing recorded answer" : selectionMode === "exact" ? "Select question" : question.response_spec?.supported_for_generation ? "Select source" : "Review only"}</label>}</div>
          <MathContent as="p" value={question.question_json.stem} />{question.diagrams?.some((diagram) => diagram.url) && <span className={s.chip}>Diagram</span>}<small>{[question.question_type.replaceAll("_", " "), `Difficulty ${question.difficulty}/5`, question.verification_status.replaceAll("_", " ")].join(" · ")}</small>
          <Button size="small" onClick={() => setDetail(question)}>Read full question</Button>
        </article>)}</div> : <EmptyState title="No questions match this branch" description="Choose a different taxonomy branch." />}
        {total > 20 && <footer className={s.pagination}><Button size="small" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous</Button><span>Page {page} of {Math.ceil(total / 20)}</span><Button size="small" disabled={offset + 20 >= total} onClick={() => setOffset(offset + 20)}>Next</Button></footer>}
      </div>
    </Dialog>
    <QuestionDetail question={detail} onClose={() => setDetail(null)} onChange={setDetail} />
  </>;
}
