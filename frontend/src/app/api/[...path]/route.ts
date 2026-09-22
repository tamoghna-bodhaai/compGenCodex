import { NextRequest, NextResponse } from "next/server";

const backendUrl = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

async function proxyToBackend(request: NextRequest) {
  const url = new URL(request.url);
  const targetUrl = `${backendUrl}${url.pathname}${url.search}`;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (["host", "connection", "content-length", "expect"].includes(lower)) return;
    headers.set(key, value);
  });

  let body: BodyInit | undefined = undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    const buf = await request.arrayBuffer();
    if (buf.byteLength > 0) {
      body = Buffer.from(buf) as unknown as BodyInit;
    }
  }

  const backendResponse = await fetch(targetUrl, {
    method: request.method,
    headers,
    body,
    // @ts-expect-error duplex required for Node fetch
    duplex: "half",
  });

  const responseHeaders = new Headers(backendResponse.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");

  // A 204 response cannot carry a body. Passing a stream here makes Next's
  // Response constructor throw and turns a successful logout into a 500.
  const responseBody = backendResponse.status === 204 || backendResponse.status === 304 ? null : backendResponse.body;
  return new NextResponse(responseBody, {
    status: backendResponse.status,
    statusText: backendResponse.statusText,
    headers: responseHeaders,
  });
}

export async function GET(request: NextRequest) {
  return proxyToBackend(request);
}
export async function POST(request: NextRequest) {
  return proxyToBackend(request);
}
export async function PUT(request: NextRequest) {
  return proxyToBackend(request);
}
export async function DELETE(request: NextRequest) {
  return proxyToBackend(request);
}
export async function PATCH(request: NextRequest) {
  return proxyToBackend(request);
}
export async function OPTIONS(request: NextRequest) {
  return proxyToBackend(request);
}
