"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, downloadPaper } from "@/lib/api";
import { isActiveGeneration, remoteDraftAction } from "@/lib/logic";
import type { BrandingProfile, Paper, PaperQuestion, QuestionDraft } from "@/lib/types";
import { useWorkspace } from "@/context/workspace-context";
import { MathContent } from "@/components/math-content";
import { PaperDialogs, type EditorDialog } from "@/components/paper-dialogs";
import { Button, EmptyState, Field, Input, JobProgress, PageHeader, Select, Textarea } from "@/components/ui";
import s from "@/styles/ui.module.css";

const LETTERS = ["A", "B", "C", "D", "E", "F"];

function draftFromQuestion(question: PaperQuestion): QuestionDraft {
  return { stem: question.question_json.stem || "", options: [...(question.question_json.options || ["", "", "", ""])], correct_answer: question.answer_json?.correct_answer || "", difficulty: question.difficulty, marks: question.question_json.marks || 4, solution: question.solution || "", section_id: question.section_id || "", custom_instruction: "" };
}

function QuestionCard({ question, index, selected, draft, onSelect }: { question: PaperQuestion; index: number; selected: boolean; draft?: QuestionDraft; onSelect: () => void }) {
  const data = draft ? { ...question.question_json, stem: draft.stem, options: draft.options } : question.question_json;
  const difficulty = draft?.difficulty ?? question.difficulty;
  const generated = question.generation_metadata?.origin === "generated";
  return <button className={`${s.questionCard} ${selected ? s.questionSelected : ""}`} onClick={onSelect}><div className={s.questionHead}><span>Q{index} · {question.question_type.replaceAll("_", " ")}</span><span className={s.chipRow}><span className={s.chip}>Difficulty {difficulty}/5</span>{question.locked && <span className={`${s.chip} ${s.chipLocked}`}>Locked</span>}{generated && <span className={`${s.chip} ${s.chipSuccess}`}>Generated</span>}</span></div><MathContent as="p" className={s.questionStem} value={data.stem} />{question.diagrams?.map((diagram) => diagram.url ? <img key={diagram.id} className={s.questionDiagram} src={diagram.url} alt={diagram.description} /> : <small key={diagram.id} className={s.muted}>Diagram unavailable: {diagram.validation_notes || diagram.validation_status}</small>)}{!!data.options?.length && <div className={s.questionOptions}>{data.options.map((option, optionIndex) => <span key={optionIndex}>{LETTERS[optionIndex]}. <MathContent value={option} /></span>)}</div>}</button>;
}

export function PaperEditorScreen({ paperId }: { paperId: string }) {
  const router = useRouter();
  const { refresh: refreshWorkspace, toast } = useWorkspace();
  const [paper, setPaper] = useState<Paper | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<QuestionDraft | null>(null);
  const [view, setView] = useState<"questions" | "answers">("questions");
  const [dialog, setDialog] = useState<EditorDialog>(null);
  const [mutationAction, setMutationAction] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [selectingBranding, setSelectingBranding] = useState(false);
  const [draftDirty, setDraftDirty] = useState(false);
  const [draftSource, setDraftSource] = useState<{ id: string; updatedAt: string } | null>(null);
  const [draftStale, setDraftStale] = useState(false);
  const [exportSettingsDirty, setExportSettingsDirty] = useState(false);
  const [brandingTemplates, setBrandingTemplates] = useState<BrandingProfile[]>([]);
  const [exportFormat, setExportFormat] = useState<"pdf" | "docx">("pdf");
  const [exportMarks, setExportMarks] = useState<number | "">("");
  const [exportMinutes, setExportMinutes] = useState<number | "">("");
  const paperRequest = useRef<AbortController | null>(null);

  const loadPaper = useCallback(async () => {
    paperRequest.current?.abort();
    const controller = new AbortController();
    paperRequest.current = controller;
    try {
      const result = await api.paper(paperId, controller.signal);
      if (controller.signal.aborted) return;
      setPaper(result); setSelectedId((current) => result.questions.some((question) => question.id === current) ? current : result.questions[0]?.id || null); setError("");
    } catch (caught) { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Couldn’t load the paper."); }
    finally { if (paperRequest.current === controller) { paperRequest.current = null; setLoading(false); } }
  }, [paperId]);
  useEffect(() => { void loadPaper(); return () => paperRequest.current?.abort(); }, [loadPaper]);
  useEffect(() => { const controller = new AbortController(); api.brandingProfiles(controller.signal).then((result) => setBrandingTemplates(result.items)).catch((caught) => { if (!controller.signal.aborted) toast(caught instanceof Error ? caught.message : "Couldn’t load branding templates.", "error"); }); return () => controller.abort(); }, [toast]);
  useEffect(() => {
    if (!paper) return;
    const selectedTemplate = brandingTemplates.find((template) => template.id === paper.branding_template_id);
    const branding = { ...(selectedTemplate?.branding_config || {}), ...(paper.branding_config || {}) };
    if (!exportSettingsDirty) {
      setExportMarks(branding.total_marks || "");
      setExportMinutes(branding.duration_minutes || "");
    }
  }, [paper, brandingTemplates, exportSettingsDirty]);
  useEffect(() => {
    if (!paper || !isActiveGeneration(paper)) return;
    let cancelled = false;
    let timer: number;
    const poll = async () => { await loadPaper(); if (!cancelled) timer = window.setTimeout(() => void poll(), 2500); };
    timer = window.setTimeout(() => void poll(), 2500);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [paper, loadPaper]);

  const refresh = useCallback(async () => { await Promise.all([loadPaper(), refreshWorkspace()]); }, [loadPaper, refreshWorkspace]);
  const selected = paper?.questions.find((question) => question.id === selectedId) || null;
  useEffect(() => {
    if (!selected) { setDraft(null); setDraftSource(null); setDraftDirty(false); setDraftStale(false); return; }
    const action = remoteDraftAction(draftSource, { id: selected.id, updatedAt: selected.updated_at }, draftDirty);
    if (action === "replace") {
      setDraft(draftFromQuestion(selected)); setDraftSource({ id: selected.id, updatedAt: selected.updated_at || "" }); setDraftDirty(false); setDraftStale(false); return;
    }
    if (action === "preserve") setDraftStale(true);
  }, [selected, draftSource, draftDirty]);

  const groups = useMemo(() => {
    if (!paper) return [];
    const map = new Map<string, { title: string; items: PaperQuestion[] }>();
    paper.sections.forEach((section) => map.set(section.id, { title: section.title, items: [] }));
    map.set("unassigned", { title: "Unsectioned questions", items: [] });
    paper.questions.forEach((question) => (map.get(question.section_id || "unassigned") || map.get("unassigned"))?.items.push(question));
    return [...map.entries()].filter(([, group]) => group.items.length || group.title !== "Unsectioned questions");
  }, [paper]);

  async function mutate(action: () => Promise<unknown>, success: string, key = "update", beforeRefresh?: () => void) {
    if (mutationAction) return false;
    setMutationAction(key); try { await action(); beforeRefresh?.(); await refresh(); toast(success); return true; }
    catch (caught) { toast(caught instanceof Error ? caught.message : "Something went wrong.", "error"); }
    finally { setMutationAction(null); }
    return false;
  }
  async function saveTitle() { const title = (document.querySelector<HTMLInputElement>("#paper-title")?.value || "").trim(); if (title) await mutate(() => api.updatePaper(paperId, { title }), "Paper title saved."); }
  async function saveQuestion(event: React.FormEvent) { event.preventDefault(); if (!selected || !draft) return; await mutate(() => api.put(`/papers/${paperId}/questions/${selected.id}`, { stem: draft.stem.trim(), options: draft.options.map((option) => option.trim()), correct_answer: draft.correct_answer.trim() || null, solution: draft.solution.trim() || null, difficulty: Number(draft.difficulty), marks: Number(draft.marks), section_id: draft.section_id || null }), "Question saved.", "save-question", () => { setDraftDirty(false); setDraftStale(false); }); }
  async function moveSection(sectionId: string) { if (!selected || !draft) return; setDraft({ ...draft, section_id: sectionId }); await mutate(() => api.put(`/papers/${paperId}/questions/${selected.id}`, { section_id: sectionId || null }), "Question moved and reordered in its section."); }
  async function addSection() { if (!paper) return; const title = window.prompt("Section title", `Section ${paper.sections.length + 1}`); if (title?.trim()) await mutate(() => api.post(`/papers/${paperId}/sections`, { title: title.trim() }), "Section added."); }
  async function exportDocument() { if (!paper || exporting) return; setExporting(true); try { await downloadPaper(paper, exportFormat, view === "answers" ? "answer_key" : "question_paper", paper.branding_template_id, { total_marks: Number(exportMarks) || null, duration_minutes: Number(exportMinutes) || null }); } catch (caught) { toast(caught instanceof Error ? caught.message : "Export failed.", "error"); } finally { setExporting(false); } }
  async function selectBrandingTemplate(templateId: string) { if (!paper || selectingBranding) return; setSelectingBranding(true); try { await api.updatePaper(paper.id, { branding_template_id: templateId || null }); setExportSettingsDirty(false); await refresh(); toast(templateId ? "Branding template selected for exports." : "Paper defaults selected for exports."); } catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t select branding.", "error"); } finally { setSelectingBranding(false); } }
  async function deletePaper() {
    if (!paper || !window.confirm(`Delete “${paper.title}”? This permanently removes the paper, its questions, and generation history.`)) return;
    try {
      setMutationAction("delete-paper");
      await api.deletePaper(paper.id);
      await refreshWorkspace();
      toast("Paper deleted.");
      router.push("/");
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t delete the paper.", "error"); }
    finally { setMutationAction(null); }
  }

  function patchDraft(next: QuestionDraft) { setDraft(next); setDraftDirty(true); }
  function reloadSelectedDraft() { if (!selected) return; setDraft(draftFromQuestion(selected)); setDraftSource({ id: selected.id, updatedAt: selected.updated_at || "" }); setDraftDirty(false); setDraftStale(false); }

  if (loading) return <section className={s.content}><EmptyState title="Opening paper" description="Loading questions, sections, and generation status…" /></section>;
  if (error || !paper) return <section className={s.content}><EmptyState title="Couldn’t open this paper" description={error || "Paper not found."} action={<Button onClick={() => void loadPaper()}>Retry</Button>} /></section>;

  const remaining = Math.max(0, (paper.requested_question_count || 0) - paper.question_count);
  const missingSolutions = paper.questions.filter((question) => !question.solution).length;
  const jobActive = isActiveGeneration(paper);
  let index = 0;
  return <section className={`${s.content} ${s.editorContent} ${view === "questions" ? s.editorQuestionsContent : ""}`}>
    <PageHeader eyebrow={`${paper.exam} · ${paper.subject}`} title={<span className={s.titleEdit}><input id="paper-title" defaultValue={paper.title} aria-label="Paper title" /><Button tone="quiet" size="small" disabled={mutationAction !== null} onClick={() => void saveTitle()}>{mutationAction === "update" ? "Saving…" : "Save title"}</Button></span>} description={<>{(paper.generation_config.topics || []).join(", ") || "No topic set"} · <strong>{paper.status}</strong></>} actions={<>{Array.isArray(paper.generation_config.reference_questions) && paper.generation_config.reference_questions.length > 0 && <Button disabled={!paper.question_count} onClick={() => setDialog("comparison")}>Compare paper</Button>}<Button onClick={() => setDialog("branding")}>Paper overrides</Button><Button disabled={!remaining || jobActive || mutationAction !== null} onClick={() => void mutate(() => api.post(`/papers/${paperId}/generate`), "Generation queued. Progress is tracked live.", "generate")}>{mutationAction === "generate" ? "Queuing…" : paper.question_count ? `Generate ${remaining} remaining` : "Generate draft"}</Button><Button disabled={!paper.question_count || jobActive || mutationAction !== null} onClick={() => void mutate(() => api.post(`/papers/${paperId}/regenerate-unlocked`), "Unlocked questions regenerated.", "regenerate-all")}>{mutationAction === "regenerate-all" ? "Regenerating…" : "Regenerate unlocked"}</Button><Button tone="danger" disabled={mutationAction !== null} onClick={() => void deletePaper()}>{mutationAction === "delete-paper" ? "Deleting…" : "Delete paper"}</Button></>} />
    <div className={s.editorCommandBar}><strong>Export</strong><span className={s.exportControls}><Select aria-label="Branding template" disabled={selectingBranding || exporting} value={paper.branding_template_id || ""} onChange={(event) => void selectBrandingTemplate(event.target.value)}><option value="">No branding / paper defaults</option>{brandingTemplates.map((template) => <option value={template.id} key={template.id}>{template.name}</option>)}</Select><Input aria-label="Export total marks" type="number" min={1} value={exportMarks} onChange={(event) => { setExportMarks(Number(event.target.value) || ""); setExportSettingsDirty(true); }} placeholder="Marks" /><Input aria-label="Export duration minutes" type="number" min={1} value={exportMinutes} onChange={(event) => { setExportMinutes(Number(event.target.value) || ""); setExportSettingsDirty(true); }} placeholder="Minutes" /><Select aria-label="Export format" value={exportFormat} onChange={(event) => setExportFormat(event.target.value as "pdf" | "docx")}><option value="pdf">PDF</option><option value="docx">DOCX</option></Select><Button tone="primary" icon="download" disabled={exporting || selectingBranding} onClick={() => void exportDocument()}>{exporting ? "Exporting…" : "Export"}</Button></span></div>
    <div className={s.editorTabs} role="tablist"><button role="tab" aria-selected={view === "questions"} className={view === "questions" ? s.editorTabActive : ""} onClick={() => setView("questions")}>Question paper</button><button role="tab" aria-selected={view === "answers"} className={view === "answers" ? s.editorTabActive : ""} onClick={() => setView("answers")}>Answer key</button></div>
    {paper.generation_job && <JobProgress job={paper.generation_job} onPause={mutationAction ? undefined : () => void mutate(() => api.post(`/papers/${paperId}/generation/pause`), "Generation paused. Completed work remains available.", "pause")} onResume={mutationAction ? undefined : () => void mutate(() => api.post(`/papers/${paperId}/generation/resume`), "Generation resumed.", "resume")} onCancel={mutationAction ? undefined : () => { if (window.confirm("Cancel this generation? Completed work will be kept.")) void mutate(() => api.post(`/papers/${paperId}/generation/cancel`), "Generation cancelled. Partial work is retained.", "cancel"); }} />}
    {view === "answers" ? <section className={`${s.panel} ${s.answerPanel}`}><div className={s.toolbar}><div><strong>Answer key & worked solutions</strong><small>{paper.question_count - missingSolutions}/{paper.question_count} solutions available</small></div><Button tone="primary" size="small" disabled={!missingSolutions || jobActive} onClick={() => void mutate(() => api.post(`/papers/${paperId}/solutions/generate`), "Solution generation queued.")}>{jobActive && paper.generation_job?.operation === "solutions" ? "Generating solutions…" : missingSolutions ? `Generate ${missingSolutions} missing solution${missingSolutions === 1 ? "" : "s"}` : "All solutions available"}</Button></div><div className={s.answerList}>{paper.questions.length ? paper.questions.map((question, questionIndex) => <article className={s.answerItem} key={question.id}><div><strong>Q{questionIndex + 1}</strong><span className={s.answerBadge}>Answer: <MathContent value={question.answer_json?.correct_answer || "Not set"} /></span></div>{question.solution ? <MathContent as="div" className={s.solutionBody} value={question.solution} /> : <p>No worked solution yet.</p>}</article>) : <EmptyState title="No questions yet" description="Generate or add questions before creating an answer key." />}</div></section> : <div className={s.editorGrid}>
      <section className={`${s.panel} ${s.questionPane}`}><div className={s.toolbar}><div className={s.inlineActions}><Button size="small" onClick={() => void addSection()}>＋ Section</Button><Button size="small" onClick={() => setDialog("source")}>Add from bank</Button><Button size="small" onClick={() => setDialog("manual")}>＋ Manual</Button></div><span className={paper.requested_question_count && paper.question_count !== paper.requested_question_count ? s.warningText : s.muted}>{paper.question_count} question{paper.question_count === 1 ? "" : "s"}{paper.requested_question_count ? ` · requested ${paper.requested_question_count}` : ""}</span></div><div className={s.questionStack}>{groups.length ? groups.map(([key, group]) => <div className={s.questionGroup} key={key}>{paper.sections.length > 0 && <div className={s.sectionLabel}>{group.title}</div>}{group.items.map((question) => <QuestionCard key={question.id} question={question} index={++index} selected={selectedId === question.id} draft={selectedId === question.id ? draft || undefined : undefined} onSelect={() => setSelectedId(question.id)} />)}</div>) : <EmptyState title="Start curating" description="Add a source question or compose one manually. Generation is available when model credentials are configured." action={<Button tone="primary" onClick={() => setDialog("source")}>Browse the seed bank</Button>} />}</div></section>
      <aside className={`${s.panel} ${s.inspector}`}>{selected && draft ? <><div className={s.inspectorHead}><div><h2>Question inspector</h2><small>{draftDirty ? "Unsaved changes" : "Live preview · changes save explicitly"}</small></div><Button size="small" tone={selected.locked ? "default" : "primary"} disabled={mutationAction !== null} onClick={() => void mutate(() => api.put(`/papers/${paperId}/questions/${selected.id}/lock`, { locked: !selected.locked }), "Question lock updated.", "lock")}>{selected.locked ? "Unlock" : "Lock question"}</Button></div><form className={`${s.inspectorForm} ${s.formGrid}`} onSubmit={saveQuestion}>{draftStale && <div className={s.staleNotice} role="status"><span>This question changed on the server. Your unsaved edits were preserved.</span><Button size="small" onClick={reloadSelectedDraft}>Reload server version</Button></div>}<Field label="Question stem"><Textarea required value={draft.stem} onChange={(event) => patchDraft({ ...draft, stem: event.target.value })} /></Field><Field label="Options"><div className={s.optionFields}>{[0, 1, 2, 3].map((optionIndex) => <label key={optionIndex}><span>{LETTERS[optionIndex]}</span><Input required value={draft.options[optionIndex] || ""} onChange={(event) => { const options = [...draft.options]; options[optionIndex] = event.target.value; patchDraft({ ...draft, options }); }} /></label>)}</div></Field><div className={s.twoCol}><Field label="Correct answer"><Input value={draft.correct_answer} onChange={(event) => patchDraft({ ...draft, correct_answer: event.target.value })} placeholder="A, B, C, or D" /></Field><Field label="Section"><Select disabled={mutationAction !== null} value={draft.section_id} onChange={(event) => void moveSection(event.target.value)}><option value="">Unsectioned</option>{paper.sections.map((section) => <option value={section.id} key={section.id}>{section.title}</option>)}</Select></Field></div><div className={s.twoCol}><Field label="Difficulty (1–5)"><Input type="number" min={1} max={5} value={draft.difficulty} onChange={(event) => patchDraft({ ...draft, difficulty: Number(event.target.value) })} /></Field><Field label="Marks"><Input type="number" min={1} max={100} value={draft.marks} onChange={(event) => patchDraft({ ...draft, marks: Number(event.target.value) })} /></Field></div><Field label="Solution / review notes"><Textarea value={draft.solution} onChange={(event) => patchDraft({ ...draft, solution: event.target.value })} placeholder="Optional; use once verified" /></Field><Field label="Custom regeneration instruction (optional)"><Textarea maxLength={1200} value={draft.custom_instruction} onChange={(event) => patchDraft({ ...draft, custom_instruction: event.target.value })} placeholder="e.g. Use a projectile-motion setup and avoid logarithms." /></Field><div className={s.formActions}><Button tone="primary" type="submit" disabled={mutationAction !== null}>{mutationAction === "save-question" ? "Saving…" : "Save changes"}</Button>{selected.generation_metadata?.reference_mapping ? <Button onClick={() => setDialog("seed-comparison")}>Compare with original</Button> : selected.generation_metadata?.origin === "generated" && Array.isArray(selected.generation_metadata?.seed_question_ids) && selected.generation_metadata.seed_question_ids.length > 0 && <Button onClick={() => setDialog("seed-comparison")}>View seeds & compare</Button>}<Button disabled={selected.locked || mutationAction !== null} onClick={() => void mutate(() => api.post(`/papers/${paperId}/regenerate-selected`, { question_ids: [selected.id], custom_instruction: draft.custom_instruction.trim() || null }), "Question regenerated.", "regenerate-question", () => { setDraftDirty(false); setDraftStale(false); })}>{mutationAction === "regenerate-question" ? "Regenerating…" : "Regenerate"}</Button><Button tone="danger" disabled={mutationAction !== null} onClick={() => { if (window.confirm("Delete this question from the paper?")) void mutate(() => api.delete(`/papers/${paperId}/questions/${selected.id}`), "Question deleted.", "delete-question"); }}>{mutationAction === "delete-question" ? "Deleting…" : "Delete"}</Button></div></form></> : <EmptyState title="Question inspector" description="Select a question to edit its content, answer key, marks, and regeneration protection." />}</aside>
    </div>}
    {dialog && <PaperDialogs kind={dialog} paper={paper} comparisonQuestion={selected} onClose={() => setDialog(null)} onChanged={refresh} toast={toast} />}
  </section>;
}
