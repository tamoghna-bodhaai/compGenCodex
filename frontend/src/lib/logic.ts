import type {
  BankSelection,
  CatalogRow,
  CreationPlan,
  CreationState,
  PaperSummary,
  QuestionType,
  SeedQuestion,
  SubtopicPlanPayload,
} from "@/lib/types";

export const EXAMS = ["JEE", "NEET"];
export const SUBJECTS = ["Mathematics", "Physics", "Chemistry"];
export const QUESTION_TYPES: Array<[QuestionType, string]> = [
  ["single_correct_mcq", "MCQ"],
  ["numerical", "Numerical"],
  ["multiple_correct_mcq", "Multiple answer"],
];
const QUESTION_TYPE_LABELS: Partial<Record<QuestionType, string>> = {
  single_correct_mcq: "MCQ",
  numerical: "Numerical",
  multiple_correct_mcq: "Multiple answer",
};
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
  seedQuestionIds: [],
  bankQuestionIds: [],
  generateVariations: true,
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

export function buildCreationPayload(creation: CreationState, bankQuestions: SeedQuestion[] = []) {
  const variationPlans: SubtopicPlanPayload[] = creation.subtopicKeys.flatMap((key) => {
    const [topic, subtopic] = splitSubtopicKey(key);
    const plan = creation.plans[key] || defaultPlan();
    if (!plan.generateVariations) return [];
    const difficulties = DIFFICULTIES.flatMap(([planKey, , difficulty]) => Array.from({ length: Number(plan.difficultyCounts[planKey]) || 0 }, () => difficulty));
    let difficultyOffset = 0;
    return QUESTION_TYPES.flatMap(([type]) => {
      const count = Number(plan.questionCounts[type]) || 0;
      if (!count) return [];
      const distribution = new Map<number, number>();
      difficulties.slice(difficultyOffset, difficultyOffset + count).forEach((difficulty) => distribution.set(difficulty, (distribution.get(difficulty) || 0) + 1));
      difficultyOffset += count;
      return [{
        topic,
        ...(subtopic ? { subtopic } : {}),
        chapters: creation.chapters,
        section_title: QUESTION_TYPE_LABELS[type] || type.replaceAll("_", " "),
        question_types: [{ type, count }],
        difficulty_distribution: [...distribution].map(([difficulty, difficultyCount]) => ({ difficulty, count: difficultyCount })),
        generation_mode: plan.generation_mode,
        variation_strength: plan.variation_strength,
        ...(plan.seedQuestionIds.length ? { seed_question_ids: plan.seedQuestionIds } : {}),
      }];
    });
  });
  const bankGroups = new Map<string, SeedQuestion[]>();
  bankQuestions.forEach((question) => {
    const key = [question.topic || "Unclassified", question.subtopic || "", question.question_type].join("::");
    bankGroups.set(key, [...(bankGroups.get(key) || []), question]);
  });
  const bankPlans: SubtopicPlanPayload[] = [...bankGroups.values()].map((questions) => {
    const first = questions[0];
    return {
      topic: first.topic || "Unclassified",
      ...(first.subtopic ? { subtopic: first.subtopic } : {}),
      chapters: [...new Set(questions.map((question) => question.chapter).filter((chapter): chapter is string => !!chapter))],
      section_title: QUESTION_TYPE_LABELS[first.question_type] || first.question_type.replaceAll("_", " "),
      question_types: [],
      difficulty_distribution: [],
      generation_mode: creation.generation_mode,
      variation_strength: creation.variation_strength,
      bank_question_ids: questions.map((question) => question.id),
    };
  }).sort((left, right) => QUESTION_TYPES.findIndex(([, label]) => label === left.section_title) - QUESTION_TYPES.findIndex(([, label]) => label === right.section_title));
  const consolidated = new Map<string, SubtopicPlanPayload>();
  [...variationPlans, ...bankPlans].forEach((plan) => {
    const key = [plan.topic, plan.subtopic || "", plan.section_title].join("::");
    const existing = consolidated.get(key);
    if (!existing) { consolidated.set(key, plan); return; }
    consolidated.set(key, {
      ...existing,
      bank_question_ids: [...new Set([...(existing.bank_question_ids || []), ...(plan.bank_question_ids || [])])],
    });
  });
  const subtopic_plans = [...consolidated.values()];
  const typeTotals = new Map<QuestionType, number>();
  const difficultyTotals = new Map<number, number>();
  for (const plan of subtopic_plans) {
    for (const item of plan.question_types) typeTotals.set(item.type, (typeTotals.get(item.type) || 0) + item.count);
    for (const item of plan.difficulty_distribution) difficultyTotals.set(item.difficulty, (difficultyTotals.get(item.difficulty) || 0) + item.count);
  }
  // Keep the plans in the request even for topic-only coverage so the backend
  // can consolidate every question into its question-type section.
  const usesTopicOnlyRequest = false;
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
