import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "./route";

describe("backend API proxy", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns a structured 502 when the backend cannot be reached", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));

    const response = await GET(
      new NextRequest("http://localhost/api/auth/session"),
    );

    expect(response.status).toBe(502);
    await expect(response.json()).resolves.toEqual({
      detail: "The application service is temporarily unavailable. Please try again.",
    });
  });
});
