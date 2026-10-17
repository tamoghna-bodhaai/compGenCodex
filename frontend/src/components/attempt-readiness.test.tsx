import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AttemptScreen } from "@/components/test-engine-screen";
import { api } from "@/lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/math-content", () => ({ MathContent: ({ value }: { value: string }) => <span>{value}</span> }));
vi.mock("@/lib/api", () => ({ ApiError: class ApiError extends Error { status = 0; }, api: { attempt: vi.fn(), saveAttemptAnswer: vi.fn(), submitAttempt: vi.fn() } }));

afterEach(() => { cleanup(); vi.clearAllMocks(); sessionStorage.clear(); });

describe("student attempt recovery", () => {
  it("preserves an unsaved answer after a failed save and permits retry", async () => {
    const now = Date.now();
    vi.mocked(api.attempt).mockResolvedValue({ id: "a1", test_id: "t1", started_at: new Date(now).toISOString(), ends_at: new Date(now + 600_000).toISOString(), server_time: new Date(now).toISOString(), submitted_at: null, score: null, revision: 0, responses: {}, questions: [{ id: "q1", section: "Physics", kind: "numerical", stem: "Enter a value", options: [], diagrams: [], response_spec: { version: 1, mode: "numeric" }, scoring: { full: 4, negative: -1, zero: 0 } }] });
    vi.mocked(api.saveAttemptAnswer).mockRejectedValueOnce(new Error("Network unavailable")).mockResolvedValueOnce({ revision: 1 });
    const user = userEvent.setup();
    render(<AttemptScreen id="a1" />);
    const answer = await screen.findByLabelText("Numeric answer");
    await user.type(answer, "42");
    await user.click(screen.getByRole("button", { name: "Save answer" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your answer is still on this screen");
    expect(answer).toHaveValue(42);
    expect(sessionStorage.getItem("cbt-draft:a1:q1")).toContain("42");
    await user.click(screen.getByRole("button", { name: "Save answer" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(api.saveAttemptAnswer).toHaveBeenLastCalledWith("a1", "q1", { answer: "42", marked_for_review: false, revision: 0 });
    expect(sessionStorage.getItem("cbt-draft:a1:q1")).toBeNull();
  });

  it("counts the visible unsaved answer in submit confirmation", async () => {
    const now = Date.now();
    vi.mocked(api.attempt).mockResolvedValue({ id: "a1", test_id: "t1", started_at: new Date(now).toISOString(), ends_at: new Date(now + 600_000).toISOString(), server_time: new Date(now).toISOString(), submitted_at: null, score: null, revision: 0, responses: {}, questions: [{ id: "q1", section: "Physics", kind: "numerical", stem: "Enter a value", options: [], diagrams: [], response_spec: { version: 1, mode: "numeric" }, scoring: { full: 4, negative: -1, zero: 0 } }] });
    const confirmation = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<AttemptScreen id="a1" />);

    await user.type(await screen.findByLabelText("Numeric answer"), "42");
    await user.click(screen.getByRole("button", { name: "Submit test" }));

    expect(confirmation).toHaveBeenCalledWith("Submit this test? 1 of 1 questions have answers.");
  });
});
