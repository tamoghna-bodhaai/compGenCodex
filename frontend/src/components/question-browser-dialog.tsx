"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { BankSelection, SeedQuestion } from "@/lib/types";
import { Dialog } from "@/components/dialog";
import { MathContent } from "@/components/math-content";
import { Button, EmptyState } from "@/components/ui";
import s from "@/styles/ui.module.css";

function breadcrumb(selection: BankSelection) {
  return [selection.exam, selection.subject, selection.chapter || undefined, selection.topic, selection.subtopic].filter(Boolean).join(" · ");
}

function QuestionDetail({ question, onClose }: { question: SeedQuestion | null; onClose: () => void }) {
  return <Dialog open={!!question} title="Seed question" subtitle={question ? [question.exam, question.subject, question.chapter].filter(Boolean).join(" · ") : ""} onClose={onClose}>
    <div className={s.modalBody}>{question && <div className={s.detailBody}>
      <div className={s.chipRow}><span className={s.chip}>{question.topic || "No topic"}</span>{question.subtopic && <span className={s.chip}>{question.subtopic}</span>}<span className={s.chip}>{question.question_type.replaceAll("_", " ")}</span><span className={s.chip}>Difficulty {question.difficulty}/5</span></div>
      <MathContent as="div" className={s.detailStem} value={question.question_json.stem} />
      {question.diagrams?.map((diagram) => diagram.url ? <img key={diagram.id} className={s.questionDiagram} src={diagram.url} alt={diagram.description} /> : <p key={diagram.id} className={s.muted}>Diagram unavailable: {diagram.validation_notes || diagram.validation_status}</p>)}
      {!!question.question_json.options?.length && <ol className={s.optionList}>{question.question_json.options.map((option, index) => <li key={index}><MathContent value={option} /></li>)}</ol>}
      <div className={s.rule} /><p>Source: {question.source_reference || question.source || "Not stated"}</p><p>Review state: {question.verification_status.replaceAll("_", " ")}</p>
      <p>{question.answer_json?.correct_answer ? <>Recorded answer: <strong>{question.answer_json.correct_answer}</strong></> : "No recorded answer; verify against the original source."}</p>
    </div>}</div>
  </Dialog>;
}

export function QuestionBrowserDialog({ open, selection, chapters, title = "Questions", selectedIds, selectable = false, onConfirm, onClose }: {
  open: boolean; selection: BankSelection; chapters?: string[]; title?: string; selectedIds?: string[]; selectable?: boolean; onConfirm?: (ids: string[]) => void; onClose: () => void;
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

  function toggle(id: string) { setChosen((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]); }
  const page = Math.floor(offset / 20) + 1;
  return <>
    <Dialog open={open} wide title={title} subtitle={breadcrumb(selection)} onClose={onClose} footer={selectable ? <div className={s.modalActions}><span className={s.muted}>{chosen.length} source question{chosen.length === 1 ? "" : "s"} selected</span><Button tone="primary" onClick={() => onConfirm?.(chosen)}>Use selected questions</Button></div> : undefined}>
      <div className={s.modalBody}><div className={s.questionBrowserHead}><p>{total} classified seed question{total === 1 ? "" : "s"}</p>{selectable && <p>Selected questions are reusable sources for generated variations.</p>}</div>
        {error ? <EmptyState title="Couldn’t load questions" description={error} /> : questions.length ? <div className={s.bankQuestionGrid}>{questions.map((question) => <article className={s.bankQuestionCard} key={question.id}>
          <div className={s.questionBrowserCardHead}><span className={s.eyebrow}>Question {question.source_reference?.match(/question (\d+)/i)?.[1] || ""}</span>{selectable && <label className={s.seedToggle}><input type="checkbox" checked={chosen.includes(question.id)} onChange={() => toggle(question.id)} /> Select source</label>}</div>
          <MathContent as="p" value={question.question_json.stem} />{question.diagrams?.some((diagram) => diagram.url) && <span className={s.chip}>Diagram</span>}<small>{[question.question_type.replaceAll("_", " "), `Difficulty ${question.difficulty}/5`, question.verification_status.replaceAll("_", " ")].join(" · ")}</small>
          <Button size="small" onClick={() => setDetail(question)}>Read full question</Button>
        </article>)}</div> : <EmptyState title="No questions match this branch" description="Choose a different taxonomy branch." />}
        {total > 20 && <footer className={s.pagination}><Button size="small" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous</Button><span>Page {page} of {Math.ceil(total / 20)}</span><Button size="small" disabled={offset + 20 >= total} onClick={() => setOffset(offset + 20)}>Next</Button></footer>}
      </div>
    </Dialog>
    <QuestionDetail question={detail} onClose={() => setDetail(null)} />
  </>;
}
