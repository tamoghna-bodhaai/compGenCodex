"use client";

import { FormEvent, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ApiError, api } from "@/lib/api";
import { Button, Field, Input } from "@/components/ui";
import s from "@/styles/ui.module.css";

export function LoginScreen() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [accessCode, setAccessCode] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const session = await api.login(email, accessCode);
      const next = searchParams.get("next");
      router.replace(session.user.role === "student" ? "/student" : next?.startsWith("/") ? next : "/");
      router.refresh();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to sign in right now.");
    } finally {
      setSubmitting(false);
    }
  }

  return <main className={s.loginPage}>
    <section className={s.loginCard} aria-labelledby="login-title">
      <span className={s.loginMark}>PS</span>
      <p className={s.eyebrow}>Private test workspace</p>
      <h1 id="login-title">Welcome to Paper Studio</h1>
      <p>Sign in with your account email and five-digit access code.</p>
      <form className={s.loginForm} onSubmit={submit}>
        <Field label="Email"><Input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required /></Field>
        <Field label="Five-digit access code"><Input type="password" value={accessCode} onChange={(event) => setAccessCode(event.target.value.replace(/\D/g, "").slice(0, 5))} inputMode="numeric" pattern="[0-9]{5}" maxLength={5} autoComplete="current-password" required autoFocus /></Field>
        {error && <p className={s.loginError} role="alert">{error}</p>}
        <Button type="submit" tone="primary" disabled={submitting}>{submitting ? "Signing in…" : "Sign in"}</Button>
      </form>
    </section>
  </main>;
}
