import { NextResponse, type NextRequest } from "next/server";

const SESSION_COOKIE_NAME = "paper_studio_session";

export function proxy(request: NextRequest) {
  if (request.nextUrl.pathname === "/login") {
    return NextResponse.next();
  }
  if (request.cookies.has(SESSION_COOKIE_NAME)) return NextResponse.next();
  const loginUrl = new URL("/login", request.url);
  loginUrl.searchParams.set("next", `${request.nextUrl.pathname}${request.nextUrl.search}`);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ["/", "/question-bank/:path*", "/new-paper/:path*", "/papers/:path*", "/login", "/tests/:path*", "/student/:path*", "/admin/:path*", "/ingestion/:path*", "/branding/:path*", "/archive/:path*"],
};
