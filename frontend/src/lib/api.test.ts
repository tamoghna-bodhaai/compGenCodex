import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiRequest } from "@/lib/api";

describe("apiRequest", () => {
  afterEach(() => vi.restoreAllMocks());

  it("preserves FastAPI detail errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ detail: "Paper is locked" }), { status: 409, headers: { "Content-Type": "application/json" } }));
    await expect(apiRequest("/papers/p")).rejects.toEqual(new ApiError("Paper is locked", 409));
  });

  it("formats FastAPI validation details instead of stringifying objects", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ detail: [{ loc: ["body", "subtopic"], msg: "String should have at least 1 character" }] }), { status: 422, headers: { "Content-Type": "application/json" } }));
    await expect(apiRequest("/papers", { method: "POST" })).rejects.toEqual(new ApiError("subtopic: String should have at least 1 character", 422));
  });

  it("serializes JSON mutation bodies with the API content type", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } }));
    await apiRequest("/papers", { method: "POST", body: JSON.stringify({ title: "Paper" }) });
    expect(fetchMock).toHaveBeenCalledWith("/api/papers", expect.objectContaining({ headers: expect.objectContaining({ "Content-Type": "application/json" }) }));
  });

  it("submits the allowlisted email and access code to login", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ authenticated: true, user: { email: "utils@bodhaai.tech" } }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const { api } = await import("@/lib/api");
    await api.login("utils@bodhaai.tech", "12345");
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/login", expect.objectContaining({
      method: "POST", body: JSON.stringify({ email: "utils@bodhaai.tech", access_code: "12345" }),
    }));
  });

  it("loads comparison seeds through the paper-scoped endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ seeds: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const { api } = await import("@/lib/api");
    await api.paperQuestionSeeds("paper-1", "question-2");
    expect(fetchMock).toHaveBeenCalledWith("/api/papers/paper-1/questions/question-2/seeds", expect.any(Object));
  });

  it("loads the persisted reference comparison for a paper", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const { api } = await import("@/lib/api");
    await api.paperComparison("paper-1");
    expect(fetchMock).toHaveBeenCalledWith("/api/papers/paper-1/comparison", expect.any(Object));
  });
});
