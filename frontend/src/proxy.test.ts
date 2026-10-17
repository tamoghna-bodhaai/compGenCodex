import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { proxy } from "@/proxy";

describe("route proxy", () => {
  it("allows the login page to render when a stale cookie exists", () => {
    const request = new NextRequest("http://localhost/login", {
      headers: { cookie: "paper_studio_session=stale" },
    });

    const response = proxy(request);

    expect(response.headers.get("location")).toBeNull();
  });

  it("redirects a protected route without a session cookie", () => {
    const request = new NextRequest("http://localhost/question-bank");

    const response = proxy(request);

    expect(response.headers.get("location")).toBe(
      "http://localhost/login?next=%2Fquestion-bank",
    );
  });
});
