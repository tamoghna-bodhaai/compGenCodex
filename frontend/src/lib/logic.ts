import type {
  BankSelection,
  CatalogRow,
  CreationPlan,
  CreationState,
  PaperSummary,
  QuestionType,
  SubtopicPlanPayload,
} from "@/lib/types";

export const EXAMS = ["JEE", "NEET"];
export const SUBJECTS = ["Mathematics", "Physics", "Chemistry"];
export const QUESTION_TYPES: Array<[QuestionType, string]> = [
  ["single_correct_mcq", "MCQ"],
  ["numerical", "Numerical"],
  ["multiple_correct_mcq", "Multiple answer"],
];
export const DIFFICULTIES: Array<["easy" | "medium" | "hard", string, number]> = [
  ["easy", "Easy", 1],
  ["medium", "Medium", 3],
  ["hard", "Hard", 5],
];

export function remoteDraftAction(source: { id: string; updatedAt: string } | null, next: { id: string; updatedAt?: string }, dirty: boolean): "replace" | "preserve" | "unchanged" {
  if (!source || source.id !== next.id) return "replace";
  if (source.updatedAt === (next.updatedAt || "")) return "unchanged";
  return dirty ? "preserve" : "replace";
}

export const defaultPlan = (): CreationPlan => ({
  questionCounts: { single_correct_mcq: 5, numerical: 0, multiple_correct_mcq: 0, subjective: 0 },
  difficultyCounts: { easy: 0, medium: 5, hard: 0 },
  generation_mode: "structural_variation",
  variation_strength: "balanced",
  sectionTitle: "",
});

export const defaultCreation = (): CreationState => ({
  title: "Definite Integrals Practice Paper",
  exam: "JEE",
  subject: "Mathematics",
  chapters: [],
  subtopicKeys: [],
  plans: {},
  generation_mode: "structural_variation",
  variation_strength: "balanced",
  total_marks: 100,
  duration_minutes: 180,
});

export function catalogRows(rows: CatalogRow[], filters: Partial<Record<keyof CatalogRow, string[]>>) {
  return rows.filter((row) => Object.entries(filters).every(([key, values]) => !values?.length || values.includes(String(row[key as keyof CatalogRow] ?? ""))));
}

export function availability(rows: CatalogRow[]) {
  return rows.reduce((total, row) => total + row.count, 0);
}

export function splitSubtopicKey(key: string): [string, string] {
  const separator = key.indexOf("::");
  return [key.slice(0, separator), key.slice(separator + 2)];
}

export function buildCreationPayload(creation: CreationState) {
  const subtopic_plans: SubtopicPlanPayload[] = creation.subtopicKeys.map((key) => {
    const [topic, subtopic] = splitSubtopicKey(key);
    const plan = creation.plans[key] || defaultPlan();
    return {
      topic,
      ...(subtopic ? { subtopic } : {}),
      chapters: creation.chapters,
      section_title: plan.sectionTitle.trim() || `${topic} › ${subtopic}`,
      question_types: QUESTION_TYPES.map(([type]) => ({ type, count: Number(plan.questionCounts[type]) || 0 })).filter((item) => item.count > 0),
      difficulty_distribution: DIFFICULTIES.map(([planKey, , difficulty]) => ({ difficulty, count: Number(plan.difficultyCounts[planKey]) || 0 })).filter((item) => item.count > 0),
      generation_mode: plan.generation_mode,
      variation_strength: plan.variation_strength,
    };
  });
  const typeTotals = new Map<QuestionType, number>();
  const difficultyTotals = new Map<number, number>();
  for (const plan of subtopic_plans) {
    for (const item of plan.question_types) typeTotals.set(item.type, (typeTotals.get(item.type) || 0) + item.count);
    for (const item of plan.difficulty_distribution) difficultyTotals.set(item.difficulty, (difficultyTotals.get(item.difficulty) || 0) + item.count);
  }
  // Older API deployments require subtopic_plans[].subtopic even though a
  // topic-only seed bank is valid. A single topic-only selection can use the
  // original top-level request shape, which both API versions understand.
  const usesTopicOnlyRequest = subtopic_plans.length === 1 && !subtopic_plans[0].subtopic;
  return {
    title: creation.title.trim(), exam: creation.exam, subject: creation.subject, chapters: creation.chapters,
    topics: [...new Set(subtopic_plans.map((item) => item.topic).filter(Boolean))],
    subtopics: [...new Set(subtopic_plans.map((item) => item.subtopic).filter(Boolean))], concepts: [],
    question_types: [...typeTotals.entries()].map(([type, count]) => ({ type, count })),
    difficulty_distribution: [...difficultyTotals.entries()].map(([difficulty, count]) => ({ difficulty, count })),
    generation_mode: creation.generation_mode, variation_strength: creation.variation_strength, subtopic_plans, usesTopicOnlyRequest,
  };
}

export function isActiveGeneration(paper: PaperSummary) {
  return ["queued", "running"].includes(paper.generation_job?.state || "") && paper.generation_job?.control_state !== "cancelled";
}

export function paperDashboardState(paper: PaperSummary): "draft" | "generating" | "ready" | "attention" | "cancelled" {
  if (paper.generation_job?.control_state === "cancelled") return "cancelled";
  if (isActiveGeneration(paper)) return "generating";
  if (paper.generation_job?.state === "failed") return "attention";
  if (["generated", "final"].includes(paper.status)) return "ready";
  return "draft";
}

export function parentBankSelection(selection: BankSelection): BankSelection {
  if (selection.subtopic) return { exam: selection.exam, subject: selection.subject, chapter: selection.chapter, topic: selection.topic };
  if (selection.topic) return { exam: selection.exam, subject: selection.subject, chapter: selection.chapter };
  if (selection.chapter) return { exam: selection.exam, subject: selection.subject };
  if (selection.subject) return { exam: selection.exam };
  return {};
}
