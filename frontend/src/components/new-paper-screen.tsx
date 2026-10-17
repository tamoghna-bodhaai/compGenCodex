"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { availability, buildCreationPayload, catalogRows, defaultCreation, defaultPlan, DIFFICULTIES, EXAMS, QUESTION_TYPES, splitSubtopicKey, SUBJECTS } from "@/lib/logic";
import type { CatalogRow, CreationPlan, CreationState, QuestionType, SeedQuestion } from "@/lib/types";
import { useWorkspace } from "@/context/workspace-context";
import { Button, EmptyState, Field, Input, PageHeader, Select } from "@/components/ui";
import { QuestionBrowserDialog } from "@/components/question-browser-dialog";
import { MathContent } from "@/components/math-content";
import s from "@/styles/ui.module.css";

function unique<T>(items: T[], key: (item: T) => string) { return [...new Map(items.map((item) => [key(item), item])).values()]; }

export function NewPaperScreen() {
  const router = useRouter();
  const { refresh, toast } = useWorkspace();
  const [catalog, setCatalog] = useState<CatalogRow[]>([]);
  const [generationCatalog, setGenerationCatalog] = useState<CatalogRow[]>([]);
  const [creation, setCreation] = useState<CreationState>(defaultCreation);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [seedPickerKey, setSeedPickerKey] = useState<string | null>(null);
  const [exactPickerOpen, setExactPickerOpen] = useState(false);
  const [selectedBankQuestions, setSelectedBankQuestions] = useState<SeedQuestion[]>([]);

  useEffect(() => { const controller = new AbortController(); Promise.all([api.catalog(controller.signal), api.catalog(controller.signal, true)]).then(([all, supported]) => { setCatalog(all.items); setGenerationCatalog(supported.items); }).catch((caught) => { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Couldn’t load the catalog."); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, []);

  const view = useMemo(() => {
    const subjectRows = catalogRows(catalog, { exam: [creation.exam], subject: [creation.subject] });
    const chapterRows = unique(subjectRows.filter((row) => row.chapter), (row) => row.chapter || "");
    const chapterScopedRows = catalogRows(catalog, { exam: [creation.exam], subject: [creation.subject], chapter: creation.chapters });
    const topicRows = unique(chapterScopedRows.filter((row) => row.topic), (row) => `${row.topic || ""}::${row.subtopic || ""}`);
    const payload = buildCreationPayload(creation, selectedBankQuestions);
    const planStates = creation.subtopicKeys.map((key) => {
      const [topic, subtopic] = splitSubtopicKey(key);
      const plan = creation.plans[key] || defaultPlan();
      const rows = catalogRows(generationCatalog, { exam: [creation.exam], subject: [creation.subject], chapter: creation.chapters, topic: [topic], subtopic: [subtopic] });
      const typeAvailability = Object.fromEntries(QUESTION_TYPES.map(([type]) => [type, availability(rows.filter((row) => row.question_type === type))])) as Record<QuestionType, number>;
      const difficultyAvailability = Object.fromEntries(DIFFICULTIES.map(([planKey, , representative]) => [planKey, availability(rows.filter((row) => representative === 1 ? row.difficulty <= 2 : representative === 3 ? row.difficulty === 3 : row.difficulty >= 4))])) as Record<"easy" | "medium" | "hard", number>;
      const requestedTypes = QUESTION_TYPES.map(([type]) => ({ type, count: Number(plan.questionCounts[type]) || 0 })).filter((item) => item.count > 0);
      const requestedDifficulties = DIFFICULTIES.map(([planKey, , difficulty]) => ({ difficulty, count: Number(plan.difficultyCounts[planKey]) || 0 })).filter((item) => item.count > 0);
      const typeTotal = requestedTypes.reduce((total, item) => total + item.count, 0);
      const difficultyTotal = requestedDifficulties.reduce((total, item) => total + item.count, 0);
      const validTypes = requestedTypes.every((item) => (typeAvailability[item.type] || 0) >= 1);
      const validDifficulties = requestedDifficulties.every((item) => { const key = DIFFICULTIES.find(([, , difficulty]) => difficulty === item.difficulty)?.[0]; return !!key && (difficultyAvailability[key] || 0) >= 1; });
      const hasSelectedSeeds = plan.seedQuestionIds.length > 0;
      return { key, topic, subtopic, plan, hasSelectedSeeds, typeAvailability, difficultyAvailability, typeTotal, difficultyTotal, validTypes, validDifficulties };
    });
    const ready = Boolean(payload.title) && (selectedBankQuestions.length > 0 || planStates.length > 0) && planStates.every((item) => item.plan.generateVariations && item.typeTotal > 0 && item.typeTotal === item.difficultyTotal && (item.hasSelectedSeeds || (item.validTypes && item.validDifficulties)));
    return { subjectRows, chapterRows, chapterScopedRows, topicRows, payload, planStates, ready };
  }, [catalog, generationCatalog, creation, selectedBankQuestions]);

  function patchCreation(patch: Partial<CreationState>) { setCreation((current) => ({ ...current, ...patch })); }
  function setChapters(chapters: string[]) {
    setCreation((current) => {
      const allowed = new Set(catalogRows(catalog, { exam: [current.exam], subject: [current.subject], chapter: chapters }).map((row) => `${row.topic || ""}::${row.subtopic || ""}`));
      const subtopicKeys = current.subtopicKeys.filter((key) => allowed.has(key));
      return { ...current, chapters, subtopicKeys, plans: Object.fromEntries(subtopicKeys.map((key) => [key, current.plans[key] || defaultPlan()])) };
    });
  }
  function toggleCoverage(key: string) {
    setCreation((current) => {
      const selected = current.subtopicKeys.includes(key);
      const subtopicKeys = selected ? current.subtopicKeys.filter((item) => item !== key) : [...current.subtopicKeys, key];
      const plans = { ...current.plans };
      if (!selected && !plans[key]) plans[key] = defaultPlan();
      return { ...current, subtopicKeys, plans };
    });
  }
  function patchPlan(key: string, patch: Partial<CreationPlan>) { setCreation((current) => ({ ...current, plans: { ...current.plans, [key]: { ...(current.plans[key] || defaultPlan()), ...patch } } })); }
  function setPlanCount(key: string, type: QuestionType, value: number) { const plan = creation.plans[key] || defaultPlan(); patchPlan(key, { questionCounts: { ...plan.questionCounts, [type]: Math.max(0, value || 0) } }); }
  function setDifficultyCount(key: string, difficulty: "easy" | "medium" | "hard", value: number) { const plan = creation.plans[key] || defaultPlan(); patchPlan(key, { difficultyCounts: { ...plan.difficultyCounts, [difficulty]: Math.max(0, value || 0) } }); }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!view.ready || submitting) return;
    setSubmitting(true);
    try {
      const { subtopic_plans, usesTopicOnlyRequest, ...basePayload } = view.payload;
      const paper = await api.createPaper(usesTopicOnlyRequest ? basePayload : { ...basePayload, subtopic_plans });
      await api.updatePaper(paper.id, { branding_config: { total_marks: creation.total_marks, duration_minutes: creation.duration_minutes } });
      try { if (view.payload.question_types.some((item) => item.count > 0)) await api.post(`/papers/${paper.id}/generate`); }
      catch (caught) { await refresh(); throw new Error(`Draft created, but generation failed: ${caught instanceof Error ? caught.message : "Unknown error"}. Use Generate draft to retry.`); }
      await refresh(); const variations = view.payload.question_types.reduce((total, item) => total + item.count, 0); toast(variations ? `${variations} variations queued; selected bank questions are ready.` : "Paper created from selected bank questions."); router.push(variations ? "/" : `/papers/${paper.id}`);
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t create the paper.", "error"); }
    finally { setSubmitting(false); }
  }

  if (loading) return <section className={s.content}><EmptyState title="Loading the question catalog" description="Preparing the available exams, topics, and seed counts…" /></section>;
  if (error) return <section className={s.content}><EmptyState title="Couldn’t load the catalog" description={error} /></section>;

  const variationTotal = view.payload.question_types.reduce((sum, item) => sum + item.count, 0);
  const bankTotal = selectedBankQuestions.length;
  const total = variationTotal + bankTotal;
  return <section className={`${s.content} ${s.narrowContent}`}>
    <PageHeader eyebrow="Create paper" title="Build a precise paper request" description="Choose individual bank questions and optionally add generated variations. Sections are organized by question type." />
    <form className={s.creationForm} onSubmit={submit}>
      <section className={s.formSection}><div className={s.formSectionHead}><span>01</span><div><h2>Paper details</h2><p>Name the paper and choose its exam context.</p></div></div><div className={s.formGrid}><Field label="Paper title"><Input required value={creation.title} onChange={(event) => patchCreation({ title: event.target.value })} /></Field><div className={s.twoCol}><Field label="Exam"><Select value={creation.exam} onChange={(event) => { patchCreation({ exam: event.target.value, chapters: [], subtopicKeys: [], plans: {} }); setSelectedBankQuestions([]); }}>{EXAMS.map((exam) => <option key={exam} value={exam} disabled={!availability(catalogRows(catalog, { exam: [exam] }))}>{exam} ({availability(catalogRows(catalog, { exam: [exam] }))} seeds)</option>)}</Select></Field><Field label="Subject"><Select value={creation.subject} onChange={(event) => { patchCreation({ subject: event.target.value, chapters: [], subtopicKeys: [], plans: {} }); setSelectedBankQuestions([]); }}>{SUBJECTS.map((subject) => <option key={subject} value={subject} disabled={!availability(catalogRows(catalog, { exam: [creation.exam], subject: [subject] }))}>{subject} ({availability(catalogRows(catalog, { exam: [creation.exam], subject: [subject] }))} seeds)</option>)}</Select></Field></div></div></section>

      <section className={s.formSection}><div className={s.formSectionHead}><span>02</span><div><h2>Paper timing &amp; marks</h2><p>These settings are saved before generation and become the default for export.</p></div></div><div className={s.twoCol}><Field label="Total marks"><Input type="number" min={1} max={1000} value={creation.total_marks} onChange={(event) => patchCreation({ total_marks: Math.max(1, Number(event.target.value) || 1) })} /></Field><Field label="Time allowed (minutes)"><Input type="number" min={1} max={1440} value={creation.duration_minutes} onChange={(event) => patchCreation({ duration_minutes: Math.max(1, Number(event.target.value) || 1) })} /></Field></div></section>

      <section className={s.formSection}><div className={s.formSectionHead}><span>03</span><div><h2>Choose questions</h2><p>Select individual questions from the bank. The paper groups them into MCQ, numerical, and multiple-answer sections automatically.</p></div></div><div className={s.questionSelectionToolbar}><div><strong>{selectedBankQuestions.length} selected</strong><p className={s.muted}>{selectedBankQuestions.length ? "Remove any question below or reopen the bank to change the selection." : "No bank questions selected yet."}</p></div><Button tone="primary" onClick={() => setExactPickerOpen(true)}>{selectedBankQuestions.length ? "Edit selection" : "Browse question bank"}</Button></div>{selectedBankQuestions.length > 0 && <div className={s.selectedQuestionList}>{selectedBankQuestions.map((question, index) => <article key={question.id} className={s.selectedQuestionItem}><span>{index + 1}</span><div><strong>{QUESTION_TYPES.find(([type]) => type === question.question_type)?.[1] || question.question_type.replaceAll("_", " ")}</strong><MathContent as="p" value={question.question_json.stem} /><small>{[question.chapter, question.topic, question.subtopic].filter(Boolean).join(" · ")}</small></div><Button size="small" tone="danger" aria-label={`Remove selected question ${index + 1}`} onClick={() => setSelectedBankQuestions((current) => current.filter((item) => item.id !== question.id))}>Remove</Button></article>)}</div>}</section>

      <section className={s.formSection}><div className={s.formSectionHead}><span>04</span><div><h2>Optional generated questions</h2><p>Use chapters and subtopics only to define source coverage for generated variations—not as paper sections.</p></div></div><div className={s.twoCol}><Field label="Chapters" hint="Choose coverage only if you want generated variations."><div className={s.countGrid}>{view.chapterRows.map((row) => { const chapter = row.chapter || ""; const count = availability(view.subjectRows.filter((item) => item.chapter === chapter)); return <label className={s.countPicker} key={chapter}><input type="checkbox" checked={creation.chapters.includes(chapter)} disabled={!count} onChange={(event) => setChapters(event.target.checked ? [...creation.chapters, chapter] : creation.chapters.filter((item) => item !== chapter))} /><span>{chapter}<small>{count} seeds</small></span></label>; })}</div></Field><Field label="Source coverage" hint={`${creation.subtopicKeys.length} generation source${creation.subtopicKeys.length === 1 ? "" : "s"} selected.`}><div className={s.countGrid}>{view.topicRows.map((row) => { const key = `${row.topic || ""}::${row.subtopic || ""}`; const count = availability(view.chapterScopedRows.filter((item) => item.topic === row.topic && item.subtopic === row.subtopic)); return <label className={s.countPicker} key={key}><input type="checkbox" checked={creation.subtopicKeys.includes(key)} disabled={!count} onChange={() => toggleCoverage(key)} /><span>{row.subtopic ? `${row.topic} › ${row.subtopic}` : row.topic}<small>{count} seeds</small></span></label>; })}</div>{!creation.chapters.length && <p className={s.muted}>Choose a chapter to configure generated variations, or skip this step.</p>}</Field></div></section>

      <section className={s.formSection}><div className={s.formSectionHead}><span>05</span><div><h2>Generated variations</h2><p>Set counts for each source coverage. Generated and selected questions are consolidated into question-type sections.</p></div></div><div className={s.planList}>{view.planStates.length ? view.planStates.map((item, index) => { const hasSelectedSeeds = item.hasSelectedSeeds; const valid = item.typeTotal > 0 && item.typeTotal === item.difficultyTotal && (hasSelectedSeeds || (item.validTypes && item.validDifficulties)); const coverageName = item.subtopic ? `${item.topic} › ${item.subtopic}` : item.topic; const hint = !item.typeTotal ? "Set at least one question." : item.typeTotal !== item.difficultyTotal ? `Type total (${item.typeTotal}) must equal difficulty total (${item.difficultyTotal}).` : hasSelectedSeeds ? `${item.typeTotal} variations will use all ${item.plan.seedQuestionIds.length} selected source question${item.plan.seedQuestionIds.length === 1 ? "" : "s"}.` : !item.validTypes || !item.validDifficulties ? "Each chosen type and difficulty needs at least one compatible source." : `${item.typeTotal} generated questions configured.`; return <article className={s.planCard} key={item.key}><div className={s.planTitle}><span>Source coverage {index + 1}</span><strong>{coverageName}</strong></div><section className={s.seedSourcePanel}><div><strong>Source questions for variations</strong><p>{hasSelectedSeeds ? `${item.plan.seedQuestionIds.length} selected as generation sources.` : "Optional: choose exact source questions for tighter control."}</p></div><Button size="small" onClick={() => setSeedPickerKey(item.key)}>{hasSelectedSeeds ? "Edit sources" : "Choose sources"}</Button></section><div className={s.twoCol}><Field label="Questions by type"><div className={s.countGrid}>{QUESTION_TYPES.map(([type, label]) => <label className={`${s.countPicker} ${!item.typeAvailability[type] && !hasSelectedSeeds ? s.unavailable : ""}`} key={type}><span>{label}<small>{item.typeAvailability[type]} seeds</small></span><Input type="number" min={0} disabled={!item.typeAvailability[type] && !hasSelectedSeeds} value={item.plan.questionCounts[type] || 0} onChange={(event) => setPlanCount(item.key, type, Number(event.target.value))} /></label>)}</div></Field><Field label="Questions by difficulty"><div className={s.countGrid}>{DIFFICULTIES.map(([key, label]) => <label className={`${s.countPicker} ${!item.difficultyAvailability[key] && !hasSelectedSeeds ? s.unavailable : ""}`} key={key}><span>{label}<small>{item.difficultyAvailability[key]} seeds</small></span><Input type="number" min={0} disabled={!item.difficultyAvailability[key] && !hasSelectedSeeds} value={item.plan.difficultyCounts[key] || 0} onChange={(event) => setDifficultyCount(item.key, key, Number(event.target.value))} /></label>)}</div></Field></div><div className={s.twoCol}><Field label="Variation mode"><Select value={item.plan.generation_mode} onChange={(event) => patchPlan(item.key, { generation_mode: event.target.value as CreationPlan["generation_mode"] })}><option value="structural_variation">Structural variation</option><option value="concept_variation">Concept variation</option></Select></Field><Field label="Variation strength"><Select value={item.plan.variation_strength} onChange={(event) => patchPlan(item.key, { variation_strength: event.target.value as CreationPlan["variation_strength"] })}><option value="close">Close to source</option><option value="balanced">Balanced</option><option value="high">High variation</option></Select></Field></div><p className={valid ? s.validText : s.warningText}>{hint}</p></article>; }) : <div className={s.inlineEmpty}>No generated variations configured. Selected bank questions can still be used as-is.</div>}</div></section>

      <footer className={s.stickyFormActions}><div><strong>{total || 0} questions configured · {bankTotal} from bank · {variationTotal} variations</strong><span className={view.ready ? s.validText : s.warningText}>{view.ready ? "Ready to create paper" : "Select at least one question or configure generated variations"}</span></div><div><Button type="button" onClick={() => router.push("/")}>Cancel</Button><Button tone="primary" type="submit" disabled={!view.ready || submitting}>{submitting ? "Creating…" : `Create paper${total ? ` (${total})` : ""}`}</Button></div></footer>
    </form>
    <QuestionBrowserDialog open={exactPickerOpen} selection={{ exam: creation.exam, subject: creation.subject }} title="Choose questions" selectable selectionMode="exact" selectedIds={selectedBankQuestions.map((question) => question.id)} selectedQuestions={selectedBankQuestions} onConfirm={(_, questions) => { setSelectedBankQuestions(questions); setExactPickerOpen(false); }} onClose={() => setExactPickerOpen(false)} />
    {seedPickerKey && (() => { const [topic, subtopic] = splitSubtopicKey(seedPickerKey); const plan = creation.plans[seedPickerKey] || defaultPlan(); return <QuestionBrowserDialog open selection={{ exam: creation.exam, subject: creation.subject, topic, ...(subtopic ? { subtopic } : {}) }} chapters={creation.chapters} title="Choose source questions" selectable selectionMode="source" selectedIds={plan.seedQuestionIds} onConfirm={(ids) => { patchPlan(seedPickerKey, { seedQuestionIds: ids }); setSeedPickerKey(null); }} onClose={() => setSeedPickerKey(null)} />; })()}
  </section>;
}
