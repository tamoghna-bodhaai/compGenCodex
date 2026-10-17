import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QuestionBrowserDialog } from "@/components/question-browser-dialog";
import { api } from "@/lib/api";
import type { SeedQuestion } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  api: { questions: vi.fn() },
}));

vi.mock("@/context/workspace-context", () => ({
  useWorkspace: () => ({ toast: vi.fn(), role: "teacher" }),
}));

function question(id: string, number: number, answer: string | null, verification_status = "approved"): SeedQuestion {
  return {
    id,
    exam: "JEE",
    subject: "Physics",
    chapter: "Mechanics",
    topic: "Work and Energy",
    subtopic: null,
    question_type: "single_correct_mcq",
    difficulty: 3,
    source_reference: `source.pdf - question ${number}`,
    question_json: { stem: `Question ${number}`, options: ["A", "B", "C", "D"] },
    answer_json: answer ? { correct_answer: answer } : null,
    solution: null,
    verification_status,
    response_spec: { version: 1, mode: "select", supported_for_generation: true },
    diagrams: [],
  };
}

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("question selection", () => {
  it("keeps selections across pages and blocks questions that are not authoring-ready", async () => {
    const first = question("q1", 1, null, "pending_review");
    const second = question("q2", 21, "B");
    vi.mocked(api.questions).mockImplementation(async (query) => ({
      items: query.get("offset") === "20" ? [second] : [first],
      total: 21,
      offset: Number(query.get("offset") || 0),
    }));
    const onConfirm = vi.fn();
    const user = userEvent.setup();
    render(<QuestionBrowserDialog open selection={{ exam: "JEE", subject: "Physics" }} title="Choose questions" selectable selectionMode="exact" onConfirm={onConfirm} onClose={vi.fn()} />);

    const firstCheckbox = await screen.findByRole("checkbox", { name: /complete source review first/i });
    expect(firstCheckbox).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next" }));
    const secondCheckbox = await screen.findByRole("checkbox", { name: /^select question$/i });
    await user.click(secondCheckbox);
    expect(screen.getByText("1 question selected")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Use selected questions" }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith(["q2"], [second]));
  });
});
