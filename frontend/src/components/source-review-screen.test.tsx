import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SourceReviewScreen } from "@/components/source-review-screen";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { ingestionReport: vi.fn(), sourceSolutionJob: vi.fn(), bankSolutionJob: vi.fn() },
}));
vi.mock("@/context/workspace-context", () => ({ useWorkspace: () => ({ toast: vi.fn(), refresh: vi.fn() }) }));
vi.mock("@/components/math-content", () => ({ MathContent: ({ value }: { value: string }) => <span>{value}</span> }));

function question(id: string, number: number, page: number, answer: string | null) {
  return { id, source_question_number: number, source_reference: `source.pdf - page ${page}, question ${number}`, chapter: "Physics", topic: "Energy", question_type: "single_correct_mcq", verification_status: "pending_review", question_json: { stem: `Question ${id}`, options: [] }, answer_json: answer ? { correct_answer: answer } : null, solution: answer ? "Worked solution" : null, response_spec: { mode: "select", supported_for_generation: true }, diagrams: [] };
}

function report(firstAnswer: string | null) {
  return { job: { source_name: "source.pdf" }, summary: { accepted: 3, review_needed: 0, diagrams: 0 }, questions: [question("first", 1, 1, firstAnswer), question("second", 1, 5, null), question("third", 2, 6, null)], formats: {}, taxonomy: {}, groups: {}, chunks: [], mappings: [], attachments: [], candidates: [] };
}

afterEach(() => { vi.useRealTimers(); vi.resetAllMocks(); });

describe("source review answer progress", () => {
  it("refreshes the report after a job finishes and distinguishes duplicate numbers", async () => {
    vi.useFakeTimers();
    vi.mocked(api.sourceSolutionJob).mockResolvedValue({ job: { id: "job", state: "running", total_questions: 2, completed_questions: 0, items: [] } } as never);
    vi.mocked(api.ingestionReport).mockResolvedValueOnce(report(null) as never).mockResolvedValueOnce(report("B") as never);
    let finishJob!: (value: unknown) => void;
    vi.mocked(api.bankSolutionJob).mockImplementation(() => new Promise((resolve) => { finishJob = resolve; }) as never);

    await act(async () => { render(<SourceReviewScreen jobId="source" />); });
    expect(screen.getByText(/3 of 3 questions missing recorded answers/)).toHaveTextContent("Q1 (page 1, item 1), Q1 (page 5, item 2), Q2");

    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
    expect(api.ingestionReport).toHaveBeenCalledTimes(1);
    await act(async () => {
      finishJob({ id: "job", state: "failed", total_questions: 2, completed_questions: 2, items: [{ question_id: "first", state: "succeeded" }, { question_id: "third", state: "failed" }] });
    });
    const missing = screen.getByText(/2 of 3 questions missing recorded answers/);
    expect(missing).toHaveTextContent("Q1 (page 5, item 2), Q2");
    expect(missing).not.toHaveTextContent("Q1 (page 1, item 1)");
    expect(screen.getByRole("region", { name: "Solution for Q1 (page 1, item 1)" })).toHaveTextContent("Answer: B");
    expect(api.ingestionReport).toHaveBeenCalledTimes(2);
  });
});
