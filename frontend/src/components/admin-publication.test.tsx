import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AdminScreen } from "@/components/test-engine-screen";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    teachers: vi.fn().mockResolvedValue({ items: [] }),
    banks: vi.fn().mockResolvedValue({ items: [{ id: "bank", name: "Physics", status: "draft", question_count: 0 }] }),
    ingestionJobs: vi.fn().mockResolvedValue({ items: [{ id: "source", source_name: "Gravitation.pdf", state: "succeeded" }] }),
    publishBank: vi.fn(),
  },
}));

describe("bank publication", () => {
  it("lets an admin choose to publish the whole source despite missing answers", async () => {
    vi.mocked(api.publishBank)
      .mockRejectedValueOnce(new Error("Missing recorded answers for Q10."))
      .mockResolvedValueOnce({ published_count: 111, missing_answer_count: 1 });
    const user = userEvent.setup();
    render(<AdminScreen />);
    await screen.findByText("Gravitation.pdf");
    await user.selectOptions(screen.getByRole("combobox"), "source");
    await user.click(screen.getByRole("button", { name: "Publish questions" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Missing recorded answers for Q10");
    expect(api.publishBank).toHaveBeenNthCalledWith(1, "bank", { source_job_id: "source", question_ids: [], allow_missing_answers: false });

    await user.click(screen.getByRole("checkbox", { name: "Publish even if answers are missing" }));
    await user.click(screen.getByRole("button", { name: "Publish questions" }));
    expect(api.publishBank).toHaveBeenNthCalledWith(2, "bank", { source_job_id: "source", question_ids: [], allow_missing_answers: true });
    expect(await screen.findByRole("status")).toHaveTextContent("Published 111 questions. 1 without recorded answers.");
    expect(screen.getByRole("checkbox", { name: "Publish even if answers are missing" })).not.toBeChecked();
  });
});
