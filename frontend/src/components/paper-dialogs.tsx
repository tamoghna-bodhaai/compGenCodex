"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { BrandingConfig, Paper, PaperComparison, PaperQuestion, ReferenceSnapshot, SeedComparison, SeedQuestion } from "@/lib/types";
import { Dialog } from "@/components/dialog";
import { MathContent } from "@/components/math-content";
import { Button, Field, Input, Select, Textarea } from "@/components/ui";
import s from "@/styles/ui.module.css";

export type EditorDialog = "source" | "manual" | "branding" | "seed-comparison" | "comparison" | null;

function stemTerms(value: string) {
  return new Set((value.toLowerCase().match(/[a-z0-9]{3,}/g) || []).filter((term) => !["with", "then", "from", "that", "find"].includes(term)));
}

function sharedStemTerms(generated: string, seed: string) {
  const generatedTerms = stemTerms(generated);
  return [...stemTerms(seed)].filter((term) => generatedTerms.has(term)).sort();
}

function referenceLabel(reference: ReferenceSnapshot | null | undefined) {
  if (!reference) return "Original unavailable";
  return reference.source_question_number ? `Original Q${reference.source_question_number}` : `Original #${reference.reference_source_position || "?"}`;
}

function QuestionPreview({ title, stem, options }: { title: string; stem: string; options?: string[] }) {
  return <article><h3>{title}</h3><MathContent as="div" className={s.detailStem} value={stem} />{!!options?.length && <ol className={s.optionList}>{options.map((option, index) => <li key={index}><MathContent value={option} /></li>)}</ol>}</article>;
}

async function imageAsDataUrl(file: File | null) {
  if (!file?.size) return null;
  if (file.size > 750 * 1024) throw new Error("Use a logo smaller than 750 KB.");
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("The selected logo could not be read."));
    reader.readAsDataURL(file);
  });
}

export function PaperDialogs({ kind, paper, comparisonQuestion, onClose, onChanged, toast }: { kind: EditorDialog; paper: Paper; comparisonQuestion?: PaperQuestion | null; onClose: () => void; onChanged: () => Promise<void>; toast: (message: string, tone?: "default" | "error") => void }) {
  const [sourceQuestions, setSourceQuestions] = useState<SeedQuestion[]>([]);
  const [sourceFilter, setSourceFilter] = useState("");
  const [branding, setBranding] = useState<BrandingConfig>(paper.branding_config || {});
  const [logoFile, setLogoFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [comparison, setComparison] = useState<SeedComparison | null>(null);
  const [paperComparison, setPaperComparison] = useState<PaperComparison | null>(null);
  const [comparisonError, setComparisonError] = useState("");
  const [selectedSeedIndex, setSelectedSeedIndex] = useState(0);
  const [selectedPaperComparisonIndex, setSelectedPaperComparisonIndex] = useState(0);

  useEffect(() => {
    if (kind === "source" && !sourceQuestions.length) api.questions(new URLSearchParams({ limit: "100" })).then((result) => setSourceQuestions(result.items)).catch((error) => toast(error.message, "error"));
    if (kind === "branding") setBranding(paper.branding_config || {});
    if (kind === "seed-comparison" && comparisonQuestion) {
      setComparison(null); setComparisonError(""); setSelectedSeedIndex(0);
      api.paperQuestionSeeds(paper.id, comparisonQuestion.id).then(setComparison).catch((error) => setComparisonError(error instanceof Error ? error.message : "Couldn’t load the referenced seed questions."));
    }
    if (kind === "comparison") {
      setPaperComparison(null); setComparisonError(""); setSelectedPaperComparisonIndex(0);
      api.paperComparison(paper.id).then(setPaperComparison).catch((error) => setComparisonError(error instanceof Error ? error.message : "Couldn’t load the reference comparison."));
    }
  }, [kind, paper.id, paper.branding_config, comparisonQuestion, comparisonQuestion?.id, sourceQuestions.length, toast]);

  const filteredSources = useMemo(() => { const query = sourceFilter.toLowerCase(); return sourceQuestions.filter((question) => !query || `${question.primary_concept || ""} ${question.question_json.stem}`.toLowerCase().includes(query)); }, [sourceFilter, sourceQuestions]);

  async function addSource(question: SeedQuestion) {
    setSubmitting(true);
    try {
      await api.post(`/papers/${paper.id}/questions/manual`, { question_type: "single_correct_mcq", stem: question.question_json.stem, options: question.question_json.options || [], correct_answer: null, solution: null, difficulty: question.difficulty, marks: question.marks || 3, primary_concept: question.primary_concept || null, secondary_concepts: question.secondary_concepts || [], estimated_time_minutes: question.expected_time_minutes || 3, section_id: null });
      await onChanged(); onClose(); toast("Source question added. Verify the answer before export.");
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t add the source question.", "error"); }
    finally { setSubmitting(false); }
  }

  async function addManual(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSubmitting(true); const values = new FormData(event.currentTarget);
    try {
      await api.post(`/papers/${paper.id}/questions/manual`, { question_type: "single_correct_mcq", stem: String(values.get("stem") || "").trim(), options: [0, 1, 2, 3].map((index) => String(values.get(`option${index}`) || "").trim()), correct_answer: String(values.get("correct_answer") || "").trim() || null, solution: null, difficulty: Number(values.get("difficulty")), marks: Number(values.get("marks")), primary_concept: null, secondary_concepts: [], estimated_time_minutes: null, section_id: String(values.get("section_id") || "") || null });
      await onChanged(); onClose(); toast("Manual question added.");
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t add the question.", "error"); }
    finally { setSubmitting(false); }
  }

  async function saveBranding() {
    setSubmitting(true);
    try {
      const logo_data_url = await imageAsDataUrl(logoFile) || branding.logo_data_url || null;
      const nextBranding = { ...branding, logo_data_url, duration_minutes: Number(branding.duration_minutes) || null, total_marks: Number(branding.total_marks) || null, instructions: branding.instructions || [] };
      await api.updatePaper(paper.id, { branding_config: nextBranding });
      await onChanged(); onClose(); toast("Branding saved for future exports.");
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Couldn’t save branding.", "error"); }
    finally { setSubmitting(false); }
  }

  const patchBranding = <K extends keyof BrandingConfig>(key: K, value: BrandingConfig[K]) => setBranding((current) => ({ ...current, [key]: value }));
  const selectedSeed = comparison?.seeds[selectedSeedIndex];
  const generatedQuestion = comparison?.question;
  const selectedReference = comparison?.reference || null;
  const comparisonSourceStem = selectedReference?.stem || selectedSeed?.question_json.stem || "";
  const sharedTerms = generatedQuestion && comparisonSourceStem ? sharedStemTerms(generatedQuestion.question_json.stem, comparisonSourceStem) : [];
  const comparisonRows = generatedQuestion && (selectedReference || selectedSeed) ? [
    ["Question type", generatedQuestion.question_type.replaceAll("_", " "), (selectedReference || selectedSeed)?.question_type.replaceAll("_", " ") || "Not recorded"],
    ["Difficulty", `${generatedQuestion.difficulty}/5`, `${(selectedReference || selectedSeed)?.difficulty || "Not recorded"}/5`],
    ["Primary concept", generatedQuestion.question_json.primary_concept || "Not recorded", selectedReference?.primary_concept || selectedSeed?.primary_concept || "Not recorded"],
    ["Options", `${generatedQuestion.question_json.options?.length || 0} options`, `${selectedReference?.options?.length || selectedSeed?.question_json.options?.length || 0} options`],
    ["Answer", generatedQuestion.answer_json?.correct_answer || "Not recorded", selectedReference?.correct_answer || selectedSeed?.answer_json?.correct_answer || "Not recorded"],
  ] : [];
  const selectedPaperItem = paperComparison?.items[selectedPaperComparisonIndex];
  const selectedPaperReference = selectedPaperItem?.reference;
  const selectedPaperGenerated = selectedPaperItem?.generated_question;
  const paperSharedTerms = selectedPaperGenerated && selectedPaperReference ? sharedStemTerms(selectedPaperGenerated.question_json.stem, selectedPaperReference.stem) : [];
  return <>
    <Dialog open={kind === "source"} title="Seed question bank" subtitle="Add a classified seed question without inventing an answer." onClose={onClose} wide><div className={s.modalBody}><Field label="Filter questions"><Input value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)} placeholder="Search a concept or question text" /></Field><div className={s.sourceList}>{filteredSources.map((question) => <article className={s.sourceItem} key={question.id}><div className={s.chipRow}><span className={s.chip}>Source Q{question.source_reference?.match(/question (\d+)/i)?.[1] || ""}</span><span className={s.chip}>Difficulty {question.difficulty}/5</span><span className={s.chip}>{question.primary_concept || "Classified seed"}</span></div><MathContent as="p" value={question.question_json.stem} /><footer><span>{question.question_json.options?.length || 0} options · answer not verified</span><Button tone="primary" size="small" disabled={submitting} onClick={() => void addSource(question)}>Add to paper</Button></footer></article>)}</div></div></Dialog>

    <Dialog open={kind === "manual"} title="Add manual question" subtitle="Single-correct MCQ with four choices." onClose={onClose}><form className={`${s.modalBody} ${s.formGrid}`} onSubmit={addManual}><Field label="Question stem"><Textarea name="stem" required placeholder="Write the question stem" /></Field><Field label="Options"><div className={s.optionFields}>{["A", "B", "C", "D"].map((letter, index) => <label key={letter}><span>{letter}</span><Input name={`option${index}`} required /></label>)}</div></Field><div className={s.twoCol}><Field label="Correct answer"><Input name="correct_answer" placeholder="A, B, C, or D" /></Field><Field label="Section"><Select name="section_id"><option value="">Unsectioned</option>{paper.sections.map((section) => <option key={section.id} value={section.id}>{section.title}</option>)}</Select></Field></div><div className={s.twoCol}><Field label="Difficulty"><Input type="number" name="difficulty" min={1} max={5} defaultValue={3} /></Field><Field label="Marks"><Input type="number" name="marks" min={1} max={100} defaultValue={4} /></Field></div><div className={s.modalActions}><Button type="button" onClick={onClose}>Cancel</Button><Button tone="primary" type="submit" disabled={submitting}>{submitting ? "Adding…" : "Add question"}</Button></div></form></Dialog>

    <Dialog open={kind === "seed-comparison"} title={comparison?.comparison_mode === "reference" ? "Original question & comparison" : "Seed questions & comparison"} subtitle={comparison?.comparison_mode === "reference" ? "Review the generated variation against the uploaded question it was based on." : "Review the current paper question against the seed questions used during generation."} onClose={onClose} wide><div className={s.modalBody}>{comparisonError ? <p className={s.errorText} role="alert">{comparisonError}</p> : !comparison ? <p className={s.muted}>Loading comparison…</p> : comparison.comparison_mode === "reference" ? !selectedReference || !generatedQuestion ? <p className={s.muted}>The uploaded original is no longer available for this question.</p> : <div className={s.comparisonBody}><div className={s.comparisonMeta}><span className={s.chip}>{referenceLabel(selectedReference)}</span><span className={s.chip}>{comparison.reference_mapping?.reference_reused ? "Reference reused" : "Ordered match"}</span><span className={s.chip}>Generation similarity {Number(comparison.generation_metadata.similarity_score || 0).toFixed(2)}</span></div><div className={s.comparisonColumns}><QuestionPreview title="Generated variation" stem={generatedQuestion.question_json.stem} options={generatedQuestion.question_json.options} /><QuestionPreview title={referenceLabel(selectedReference)} stem={selectedReference.stem} options={selectedReference.options} /></div><div className={s.comparisonFields}>{comparisonRows.map(([label, generated, original]) => <div key={label}><span>{label}</span><strong>{generated}</strong><strong>{original}</strong><em className={generated === original ? s.comparisonSame : s.comparisonChanged}>{generated === original ? "Same" : "Changed"}</em></div>)}</div><section className={s.overlapPanel}><strong>Surface wording overlap</strong><p>Shared normalized stem terms only; this is not a semantic-similarity judgment.</p><div className={s.chipRow}>{sharedTerms.length ? sharedTerms.map((term) => <mark key={term}>{term}</mark>) : <span className={s.muted}>No shared normalized terms</span>}</div></section></div> : !selectedSeed || !generatedQuestion ? <p className={s.muted}>No seed questions are currently available for this generated question.</p> : <div className={s.comparisonLayout}><aside className={s.seedSelector} aria-label="Referenced seed questions"><strong>Referenced seeds</strong>{comparison.seeds.map((seed, index) => <button type="button" key={seed.id} className={index === selectedSeedIndex ? s.seedSelected : ""} onClick={() => setSelectedSeedIndex(index)}>Seed {index + 1}<small>{seed.primary_concept || "Classified seed"}</small></button>)}</aside><section className={s.comparisonBody}><div className={s.comparisonMeta}><span className={s.chip}>Generation similarity {Number(comparison.generation_metadata.similarity_score || 0).toFixed(2)}</span><span className={s.chip}>Seed {selectedSeedIndex + 1} of {comparison.seeds.length}</span></div><div className={s.comparisonColumns}><QuestionPreview title="Current paper question" stem={generatedQuestion.question_json.stem} options={generatedQuestion.question_json.options} /><QuestionPreview title="Referenced seed" stem={selectedSeed.question_json.stem} options={selectedSeed.question_json.options} /></div><div className={s.comparisonFields}>{comparisonRows.map(([label, generated, seed]) => <div key={label}><span>{label}</span><strong>{generated}</strong><strong>{seed}</strong><em className={generated === seed ? s.comparisonSame : s.comparisonChanged}>{generated === seed ? "Same" : "Changed"}</em></div>)}</div><section className={s.overlapPanel}><strong>Surface wording overlap</strong><p>Shared normalized stem terms only; this is not a semantic-similarity judgment.</p><div className={s.chipRow}>{sharedTerms.length ? sharedTerms.map((term) => <mark key={term}>{term}</mark>) : <span className={s.muted}>No shared normalized terms</span>}</div></section>{comparison.missing_seed_question_ids.length > 0 && <p className={s.warningText}>{comparison.missing_seed_question_ids.length} recorded seed question{comparison.missing_seed_question_ids.length === 1 ? " is" : "s are"} no longer available in the bank.</p>}</section></div>}</div></Dialog>

    <Dialog open={kind === "comparison"} title="Compare generated paper" subtitle={paperComparison?.reference_filter ? `Ordered against original questions ${paperComparison.reference_filter}.` : "Ordered against the uploaded reference paper."} onClose={onClose} wide><div className={s.modalBody}>{comparisonError ? <p className={s.errorText} role="alert">{comparisonError}</p> : !paperComparison ? <p className={s.muted}>Loading paper comparison…</p> : !selectedPaperItem ? <p className={s.muted}>No generated questions have an available reference comparison yet.</p> : <div className={s.comparisonLayout}><aside className={s.seedSelector} aria-label="Generated and original question pairs"><strong>Generated ↔ original</strong>{paperComparison.items.map((item, index) => <button type="button" key={item.generated_question.id} className={index === selectedPaperComparisonIndex ? s.seedSelected : ""} onClick={() => setSelectedPaperComparisonIndex(index)}>Generated Q{item.generated_position} <small>{item.reference ? referenceLabel(item.reference) : "Original unavailable"}{item.mapping_status === "reused" ? " · reused" : ""}</small></button>)}</aside><section className={s.comparisonBody}><div className={s.comparisonMeta}><span className={s.chip}>{paperComparison.generated_count} generated pair{paperComparison.generated_count === 1 ? "" : "s"}</span><span className={s.chip}>{paperComparison.reference_count} selected original{paperComparison.reference_count === 1 ? "" : "s"}</span><span className={s.chip}>{selectedPaperItem.mapping_status === "reused" ? "Reference reused" : selectedPaperItem.mapping_status === "unavailable" ? "Original unavailable" : "Ordered match"}</span></div>{selectedPaperGenerated && selectedPaperReference ? <><div className={s.comparisonColumns}><QuestionPreview title={`Generated Q${selectedPaperItem.generated_position}`} stem={selectedPaperGenerated.question_json.stem} options={selectedPaperGenerated.question_json.options} /><QuestionPreview title={referenceLabel(selectedPaperReference)} stem={selectedPaperReference.stem} options={selectedPaperReference.options} /></div><div className={s.comparisonFields}><div><span>Question type</span><strong>{selectedPaperGenerated.question_type.replaceAll("_", " ")}</strong><strong>{selectedPaperReference.question_type.replaceAll("_", " ")}</strong><em className={s.comparisonChanged}>Changed</em></div><div><span>Difficulty</span><strong>{selectedPaperGenerated.difficulty}/5</strong><strong>{selectedPaperReference.difficulty}/5</strong><em className={selectedPaperGenerated.difficulty === selectedPaperReference.difficulty ? s.comparisonSame : s.comparisonChanged}>{selectedPaperGenerated.difficulty === selectedPaperReference.difficulty ? "Same" : "Changed"}</em></div></div><section className={s.overlapPanel}><strong>Surface wording overlap</strong><p>Shared normalized stem terms only; this is not a semantic-similarity judgment.</p><div className={s.chipRow}>{paperSharedTerms.length ? paperSharedTerms.map((term) => <mark key={term}>{term}</mark>) : <span className={s.muted}>No shared normalized terms</span>}</div></section></> : <p className={s.warningText}>The original snapshot for this generated question is unavailable.</p>}</section></div>}</div></Dialog>

    <Dialog open={kind === "branding"} title="Paper branding overrides" subtitle="These values supplement the selected reusable template for this paper only." onClose={onClose} wide><div className={`${s.modalBody} ${s.formGrid}`}><div className={s.brandingIntro}>{branding.logo_data_url ? <img src={branding.logo_data_url} alt="Current academy logo" /> : <span className={s.logoPlaceholder}>Logo</span>}<div><strong>{branding.institution_name || "Paper branding"}</strong><small>Create reusable templates from the Branding workspace.</small></div></div><div className={s.twoCol}><Field label="Academy / institution name"><Input value={branding.institution_name || ""} onChange={(event) => patchBranding("institution_name", event.target.value)} /></Field><Field label="Logo"><Input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => setLogoFile(event.target.files?.[0] || null)} /></Field></div><Field label="Address"><Textarea value={branding.address || ""} onChange={(event) => patchBranding("address", event.target.value)} /></Field><div className={s.twoCol}><Field label="Phone / email"><Input value={branding.contact || ""} onChange={(event) => patchBranding("contact", event.target.value)} /></Field><Field label="Duration (minutes)"><Input type="number" min={1} value={branding.duration_minutes || ""} onChange={(event) => patchBranding("duration_minutes", Number(event.target.value) || null)} /></Field></div><div className={s.twoCol}><Field label="Total marks"><Input type="number" min={1} value={branding.total_marks || ""} onChange={(event) => patchBranding("total_marks", Number(event.target.value) || null)} /></Field><Field label="Watermark"><Input value={branding.watermark_text || ""} onChange={(event) => patchBranding("watermark_text", event.target.value)} /></Field></div><Field label="Header text"><Input value={branding.header_text || ""} onChange={(event) => patchBranding("header_text", event.target.value)} /></Field><Field label="Footer text"><Input value={branding.footer_text || ""} onChange={(event) => patchBranding("footer_text", event.target.value)} /></Field><Field label="Instructions (one per line)"><Textarea value={(branding.instructions || []).join("\n")} onChange={(event) => patchBranding("instructions", event.target.value.split("\n").map((line) => line.trim()).filter(Boolean))} /></Field><div className={s.modalActions}><Button onClick={onClose}>Cancel</Button><Button tone="primary" disabled={submitting} onClick={() => void saveBranding()}>{submitting ? "Saving…" : "Save paper overrides"}</Button></div></div></Dialog>
  </>;
}
