"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { availability, buildCreationPayload, catalogRows, defaultCreation, defaultPlan, DIFFICULTIES, EXAMS, QUESTION_TYPES, splitSubtopicKey, SUBJECTS } from "@/lib/logic";
import type { CatalogRow, CreationPlan, CreationState, QuestionType } from "@/lib/types";
import { useWorkspace } from "@/context/workspace-context";
import { Button, EmptyState, Field, Input, PageHeader, Select } from "@/components/ui";
import s from "@/styles/ui.module.css";

function unique<T>(items: T[], key: (item: T) => string) { return [...new Map(items.map((item) => [key(item), item])).values()]; }

export function NewPaperScreen() {
  const router = useRouter();
  const { refresh, toast } = useWorkspace();
  const [catalog, setCatalog] = useState<CatalogRow[]>([]);
  const [creation, setCreation] = useState<CreationState>(defaultCreation);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => { const controller = new AbortController(); api.catalog(controller.signal).then((result) => setCatalog(result.items)).catch((caught) => { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Couldn’t load the catalog."); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, []);

  const view = useMemo(() => {
    const subjectRows = catalogRows(catalog, { exam: [creation.exam], subject: [creation.subject] });
    const chapterRows = unique(subjectRows.filter((row) => row.chapter), (row) => row.chapter || "");
    const chapterScopedRows = catalogRows(catalog, { exam: [creation.exam], subject: [creation.subject], chapter: creation.chapters });
    const topicRows = unique(chapterScopedRows.filter((row) => row.topic), (row) => `${row.topic || ""}::${row.subtopic || ""}`);
    const payload = buildCreationPayload(creation);
    const planStates = creation.subtopicKeys.map((key, index) => {
      const [topic, subtopic] = splitSubtopicKey(key);
      const plan = creation.plans[key] || defaultPlan();
      const rows = catalogRows(catalog, { exam: [creation.exam], subject: [creation.subject], chapter: creation.chapters, topic: [topic], subtopic: [subtopic] });
      const typeAvailability = Object.fromEntries(QUESTION_TYPES.map(([type]) => [type, availability(rows.filter((row) => row.question_type === type))])) as Record<QuestionType, number>;
      const difficultyAvailability = Object.fromEntries(DIFFICULTIES.map(([planKey, , representative]) => [planKey, availability(rows.filter((row) => representative === 1 ? row.difficulty <= 2 : representative === 3 ? row.difficulty === 3 : row.difficulty >= 4))])) as Record<"easy" | "medium" | "hard", number>;
      const planPayload = payload.subtopic_plans[index];
      const typeTotal = planPayload.question_types.reduce((total, item) => total + item.count, 0);
      const difficultyTotal = planPayload.difficulty_distribution.reduce((total, item) => total + item.count, 0);
      const validTypes = planPayload.question_types.every((item) => (typeAvailability[item.type] || 0) >= 3);
      const validDifficulties = planPayload.difficulty_distribution.every((item) => { const key = DIFFICULTIES.find(([, , difficulty]) => difficulty === item.difficulty)?.[0]; return !!key && (difficultyAvailability[key] || 0) >= 3; });
      return { key, topic, subtopic, plan, typeAvailability, difficultyAvailability, typeTotal, difficultyTotal, validTypes, validDifficulties };
    });
    const ready = Boolean(payload.title) && planStates.length > 0 && planStates.every((item) => item.typeTotal > 0 && item.typeTotal === item.difficultyTotal && item.validTypes && item.validDifficulties);
    return { subjectRows, chapterRows, chapterScopedRows, topicRows, payload, planStates, ready };
  }, [catalog, creation]);

  function patchCreation(patch: Partial<CreationState>) { setCreation((current) => ({ ...current, ...patch })); }
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
      try { await api.post(`/papers/${paper.id}/generate`); }
      catch (caught) { await refresh(); throw new Error(`Draft created, but generation failed: ${caught instanceof Error ? caught.message : "Unknown error"}. Use Generate draft to retry.`); }
      await refresh(); toast(`${view.payload.question_types.reduce((total, item) => total + item.count, 0)}-question generation queued for ${creation.total_marks} marks in ${creation.duration_minutes} minutes. Track progress on the dashboard.`); router.push("/");
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t create the paper.", "error"); }
    finally { setSubmitting(false); }
  }

  if (loading) return <section className={s.content}><EmptyState title="Loading the question catalog" description="Preparing the available exams, topics, and seed counts…" /></section>;
  if (error) return <section className={s.content}><EmptyState title="Couldn’t load the catalog" description={error} /></section>;

  const total = view.payload.question_types.reduce((sum, item) => sum + item.count, 0);
  return <section className={`${s.content} ${s.narrowContent}`}>
    <PageHeader eyebrow="Create paper" title="Build a precise paper request" description="Only catalog choices with compatible labeled seed questions can be generated." />
    <form className={s.creationForm} onSubmit={submit}>
      <section className={s.formSection}><div className={s.formSectionHead}><span>01</span><div><h2>Paper details</h2><p>Name the paper and choose its exam context.</p></div></div><div className={s.formGrid}><Field label="Paper title"><Input required value={creation.title} onChange={(event) => patchCreation({ title: event.target.value })} /></Field><div className={s.twoCol}><Field label="Exam"><Select value={creation.exam} onChange={(event) => patchCreation({ exam: event.target.value, chapters: [], subtopicKeys: [], plans: {} })}>{EXAMS.map((exam) => <option key={exam} value={exam} disabled={!availability(catalogRows(catalog, { exam: [exam] }))}>{exam} ({availability(catalogRows(catalog, { exam: [exam] }))} seeds)</option>)}</Select></Field><Field label="Subject"><Select value={creation.subject} onChange={(event) => patchCreation({ subject: event.target.value, chapters: [], subtopicKeys: [], plans: {} })}>{SUBJECTS.map((subject) => <option key={subject} value={subject} disabled={!availability(catalogRows(catalog, { exam: [creation.exam], subject: [subject] }))}>{subject} ({availability(catalogRows(catalog, { exam: [creation.exam], subject: [subject] }))} seeds)</option>)}</Select></Field></div></div></section>

      <section className={s.formSection}><div className={s.formSectionHead}><span>02</span><div><h2>Paper timing &amp; marks</h2><p>These settings are saved before generation and become the default for export.</p></div></div><div className={s.twoCol}><Field label="Total marks"><Input type="number" min={1} max={1000} value={creation.total_marks} onChange={(event) => patchCreation({ total_marks: Math.max(1, Number(event.target.value) || 1) })} /></Field><Field label="Time allowed (minutes)"><Input type="number" min={1} max={1440} value={creation.duration_minutes} onChange={(event) => patchCreation({ duration_minutes: Math.max(1, Number(event.target.value) || 1) })} /></Field></div></section>

      <section className={s.formSection}><div className={s.formSectionHead}><span>03</span><div><h2>Coverage</h2><p>Select one or more chapters and subtopics.</p></div></div><div className={s.twoCol}><Field label="Chapters" hint="Ctrl/Cmd+click to pick several chapters."><Select multiple size={5} value={creation.chapters} onChange={(event) => patchCreation({ chapters: [...event.target.selectedOptions].map((option) => option.value), subtopicKeys: [], plans: {} })}>{view.chapterRows.map((row) => { const count = availability(view.subjectRows.filter((item) => item.chapter === row.chapter)); return <option key={row.chapter} value={row.chapter || ""} disabled={!count}>{row.chapter} ({count} seeds)</option>; })}</Select></Field><Field label="Subtopics" hint="Each selected subtopic becomes one section."><Select multiple size={5} value={creation.subtopicKeys} onChange={(event) => { const keys = [...event.target.selectedOptions].map((option) => option.value); const plans = { ...creation.plans }; keys.forEach((key) => { if (!plans[key]) plans[key] = defaultPlan(); }); patchCreation({ subtopicKeys: keys, plans }); }}>{view.topicRows.map((row) => { const key = `${row.topic || ""}::${row.subtopic || ""}`; const count = availability(view.chapterScopedRows.filter((item) => item.topic === row.topic && item.subtopic === row.subtopic)); return <option key={key} value={key} disabled={!count}>{row.subtopic ? `${row.topic} › ${row.subtopic}` : row.topic} ({count} seeds)</option>; })}</Select></Field></div></section>

      <section className={s.formSection}><div className={s.formSectionHead}><span>04</span><div><h2>Section plans</h2><p>Match question-type and difficulty totals for every section.</p></div></div><div className={s.planList}>{view.planStates.length ? view.planStates.map((item, index) => { const valid = item.typeTotal > 0 && item.typeTotal === item.difficultyTotal && item.validTypes && item.validDifficulties; const coverageName = item.subtopic ? `${item.topic} › ${item.subtopic}` : item.topic; const hint = !item.typeTotal ? "Set at least one question." : item.typeTotal !== item.difficultyTotal ? `Type total (${item.typeTotal}) must equal difficulty total (${item.difficultyTotal}).` : !item.validTypes || !item.validDifficulties ? "Each chosen type/difficulty needs at least three compatible seeds." : `${item.typeTotal} questions · one section in the paper.`; return <article className={s.planCard} key={item.key}><div className={s.planTitle}><span>Section {view.planStates.length > 1 ? index + 1 : ""}</span><strong>{coverageName}</strong></div><Field label="Section title"><Input value={item.plan.sectionTitle} placeholder={coverageName} onChange={(event) => patchPlan(item.key, { sectionTitle: event.target.value })} /></Field><div className={s.twoCol}><Field label="Questions by type"><div className={s.countGrid}>{QUESTION_TYPES.map(([type, label]) => <label className={`${s.countPicker} ${!item.typeAvailability[type] ? s.unavailable : ""}`} key={type}><span>{label}<small>{item.typeAvailability[type]} seeds</small></span><Input type="number" min={0} max={100} disabled={!item.typeAvailability[type]} value={item.plan.questionCounts[type] || 0} onChange={(event) => setPlanCount(item.key, type, Number(event.target.value))} /></label>)}</div></Field><Field label="Questions by difficulty"><div className={s.countGrid}>{DIFFICULTIES.map(([key, label]) => <label className={`${s.countPicker} ${!item.difficultyAvailability[key] ? s.unavailable : ""}`} key={key}><span>{label}<small>{item.difficultyAvailability[key]} seeds</small></span><Input type="number" min={0} max={100} disabled={!item.difficultyAvailability[key]} value={item.plan.difficultyCounts[key] || 0} onChange={(event) => setDifficultyCount(item.key, key, Number(event.target.value))} /></label>)}</div></Field></div><div className={s.twoCol}><Field label="Variation mode"><Select value={item.plan.generation_mode} onChange={(event) => patchPlan(item.key, { generation_mode: event.target.value as CreationPlan["generation_mode"] })}><option value="structural_variation">Structural variation</option><option value="concept_variation">Concept variation</option></Select></Field><Field label="Variation strength"><Select value={item.plan.variation_strength} onChange={(event) => patchPlan(item.key, { variation_strength: event.target.value as CreationPlan["variation_strength"] })}><option value="close">Close to source</option><option value="balanced">Balanced</option><option value="high">High variation</option></Select></Field></div><p className={valid ? s.validText : s.warningText}>{hint}</p></article>; }) : <div className={s.inlineEmpty}>Select subtopics to configure per-section counts.</div>}</div></section>

      <footer className={s.stickyFormActions}><div><strong>{total || 0} questions configured</strong><span className={view.ready ? s.validText : s.warningText}>{view.ready ? "Ready to create and generate" : "Complete every section plan to continue"}</span></div><div><Button type="button" onClick={() => router.push("/")}>Cancel</Button><Button tone="primary" type="submit" disabled={!view.ready || submitting}>{submitting ? "Creating…" : `Create & generate${total ? ` (${total})` : ""}`}</Button></div></footer>
    </form>
  </section>;
}
