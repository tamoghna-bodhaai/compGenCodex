import { describe, expect, it } from "vitest";
import { buildCreationPayload, defaultCreation, defaultPlan, paperDashboardState, parentBankSelection, remoteDraftAction } from "@/lib/logic";
import type { PaperSummary } from "@/lib/types";

describe("paper creation payload", () => {
  it("keeps per-subtopic type and difficulty totals", () => {
    const creation = defaultCreation();
    creation.chapters = ["Calculus"];
    creation.subtopicKeys = ["Definite Integrals::Properties"];
    creation.plans[creation.subtopicKeys[0]] = defaultPlan();
    const payload = buildCreationPayload(creation);
    expect(payload.subtopic_plans[0]).toMatchObject({ topic: "Definite Integrals", subtopic: "Properties", section_title: "Definite Integrals › Properties" });
    expect(payload.question_types).toEqual([{ type: "single_correct_mcq", count: 5 }]);
    expect(payload.difficulty_distribution).toEqual([{ difficulty: 3, count: 5 }]);
  });

  it("uses the compatible top-level request shape for one topic-only plan", () => {
    const creation = defaultCreation();
    creation.chapters = ["Calculus"];
    creation.subtopicKeys = ["Definite Integrals::"];
    creation.plans[creation.subtopicKeys[0]] = defaultPlan();
    const payload = buildCreationPayload(creation);
    expect(payload.usesTopicOnlyRequest).toBe(true);
    expect(payload.subtopic_plans[0]).not.toHaveProperty("subtopic");
    expect(payload.question_types).toEqual([{ type: "single_correct_mcq", count: 5 }]);
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
