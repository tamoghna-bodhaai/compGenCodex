import { describe, expect, it } from "vitest";
import { buildCreationPayload, defaultCreation, defaultPlan, paperDashboardState, parentBankSelection, remoteDraftAction } from "@/lib/logic";
import type { PaperSummary, SeedQuestion } from "@/lib/types";

describe("paper creation payload", () => {
  it("keeps per-subtopic type and difficulty totals", () => {
    const creation = defaultCreation();
    creation.chapters = ["Calculus"];
    creation.subtopicKeys = ["Definite Integrals::Properties"];
    creation.plans[creation.subtopicKeys[0]] = defaultPlan();
    const payload = buildCreationPayload(creation);
    expect(payload.subtopic_plans[0]).toMatchObject({ topic: "Definite Integrals", subtopic: "Properties", section_title: "MCQ" });
    expect(payload.question_types).toEqual([{ type: "single_correct_mcq", count: 5 }]);
    expect(payload.difficulty_distribution).toEqual([{ difficulty: 3, count: 5 }]);
  });

  it("keeps a topic-only plan so its question-type section is preserved", () => {
    const creation = defaultCreation();
    creation.chapters = ["Calculus"];
    creation.subtopicKeys = ["Definite Integrals::"];
    creation.plans[creation.subtopicKeys[0]] = defaultPlan();
    const payload = buildCreationPayload(creation);
    expect(payload.usesTopicOnlyRequest).toBe(false);
    expect(payload.subtopic_plans[0]).not.toHaveProperty("subtopic");
    expect(payload.question_types).toEqual([{ type: "single_correct_mcq", count: 5 }]);
  });

  it("includes selected bank questions as reusable variation sources", () => {
    const creation = defaultCreation();
    creation.chapters = ["Calculus"];
    creation.subtopicKeys = ["Definite Integrals::Properties"];
    creation.plans[creation.subtopicKeys[0]] = { ...defaultPlan(), seedQuestionIds: ["seed-a", "seed-b"] };
    expect(buildCreationPayload(creation).subtopic_plans[0]).toMatchObject({ seed_question_ids: ["seed-a", "seed-b"] });
  });

  it("splits mixed generated questions into type sections without losing difficulty totals", () => {
    const creation = defaultCreation();
    creation.chapters = ["Calculus"];
    creation.subtopicKeys = ["Definite Integrals::Properties"];
    creation.plans[creation.subtopicKeys[0]] = {
      ...defaultPlan(),
      questionCounts: { single_correct_mcq: 2, numerical: 1, multiple_correct_mcq: 0, subjective: 0 },
      difficultyCounts: { easy: 1, medium: 2, hard: 0 },
    };
    const plans = buildCreationPayload(creation).subtopic_plans;
    expect(plans).toHaveLength(2);
    expect(plans[0]).toMatchObject({ section_title: "MCQ", question_types: [{ type: "single_correct_mcq", count: 2 }], difficulty_distribution: [{ difficulty: 1, count: 1 }, { difficulty: 3, count: 1 }] });
    expect(plans[1]).toMatchObject({ section_title: "Numerical", question_types: [{ type: "numerical", count: 1 }], difficulty_distribution: [{ difficulty: 3, count: 1 }] });
  });

  it("groups exact bank questions into question-type sections", () => {
    const creation = defaultCreation();
    const questions = [
      { id: "q1", chapter: "Calculus", topic: "Integrals", subtopic: null, question_type: "single_correct_mcq" },
      { id: "q2", chapter: "Calculus", topic: "Integrals", subtopic: null, question_type: "numerical" },
    ] as SeedQuestion[];
    const payload = buildCreationPayload(creation, questions);
    expect(payload.usesTopicOnlyRequest).toBe(false);
    expect(payload.subtopic_plans).toEqual(expect.arrayContaining([
      expect.objectContaining({ section_title: "MCQ", bank_question_ids: ["q1"] }),
      expect.objectContaining({ section_title: "Numerical", bank_question_ids: ["q2"] }),
    ]));
  });
});

describe("dashboard state", () => {
  const paper = { id: "p", title: "Paper", exam: "JEE", subject: "Math", status: "draft", created_at: "", updated_at: "", question_count: 0, requested_question_count: 5, generation_job: null } satisfies PaperSummary;
  it("distinguishes active and cancelled work", () => {
    expect(paperDashboardState({ ...paper, generation_job: { id: "j", paper_id: "p", operation: "initial", state: "running", total_questions: 5, completed_questions: 1 } })).toBe("generating");
    expect(paperDashboardState({ ...paper, generation_job: { id: "j", paper_id: "p", operation: "initial", state: "failed", control_state: "cancelled", total_questions: 5, completed_questions: 1 } })).toBe("cancelled");
  });
});

describe("question bank navigation", () => {
  it("moves up exactly one taxonomy level", () => {
    expect(parentBankSelection({ exam: "JEE", subject: "Mathematics", chapter: "Calculus", topic: "Integrals", subtopic: "Definite" })).toEqual({ exam: "JEE", subject: "Mathematics", chapter: "Calculus", topic: "Integrals" });
  });
});

describe("live editor reconciliation", () => {
  it("preserves dirty input when a newer server revision arrives", () => {
    expect(remoteDraftAction({ id: "q1", updatedAt: "one" }, { id: "q1", updatedAt: "two" }, true)).toBe("preserve");
  });

  it("accepts remote updates for clean drafts and selection changes", () => {
    expect(remoteDraftAction({ id: "q1", updatedAt: "one" }, { id: "q1", updatedAt: "two" }, false)).toBe("replace");
    expect(remoteDraftAction({ id: "q1", updatedAt: "one" }, { id: "q2", updatedAt: "one" }, true)).toBe("replace");
  });
});
