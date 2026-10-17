"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError, api } from "@/lib/api";
import type { CbtAttempt, CbtQuestion, CbtTest, Classroom, IngestionJob, SessionAccount, TeacherAccount } from "@/lib/types";
import { Button, Field, Input, PageHeader, Select } from "@/components/ui";
import { MathContent } from "@/components/math-content";
import s from "@/styles/test-engine.module.css";

function message(error: unknown) { return error instanceof Error ? error.message : "The request failed."; }
function date(value: string | null | undefined) { return value ? new Date(value).toLocaleString() : "Any time"; }

export function AdminScreen() {
  const [teachers, setTeachers] = useState<TeacherAccount[]>([]);
  const [banks, setBanks] = useState<Array<{ id: string; name: string; status: string; question_count: number }>>([]);
  const [sources, setSources] = useState<IngestionJob[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const refreshTeachers = useCallback(async () => { const people = await api.teachers(); setTeachers(people.items); }, []);
  const load = useCallback(async () => {
    const [people, collections, jobs] = await Promise.allSettled([api.teachers(), api.banks(), api.ingestionJobs()]);
    if (people.status === "fulfilled") setTeachers(people.value.items);
    if (collections.status === "fulfilled") setBanks(collections.value.items);
    if (jobs.status === "fulfilled") setSources(jobs.value.items.filter(job => job.state === "succeeded"));
    const failure = [people, collections, jobs].find(result => result.status === "rejected");
    if (failure?.status === "rejected") throw failure.reason;
  }, []);
  useEffect(() => { void load().catch(e => setError(message(e))); }, [load]);
  async function createTeacher(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const form = event.currentTarget;
    const body = Object.fromEntries(new FormData(form));
    setNotice("");
    try {
      await api.createTeacher(body);
      form.reset();
      setNotice("Teacher added successfully.");
      try { await refreshTeachers(); } catch { setNotice("Teacher added. Refresh the page to view the updated list."); }
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await refreshTeachers().catch(() => undefined);
      }
      setError(message(e));
    } finally { setBusy(false); }
  }
  async function createBank(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const name = String(new FormData(event.currentTarget).get("name") || "");
    try { await api.createBank(name); event.currentTarget.reset(); await load(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  async function publish(event: React.FormEvent<HTMLFormElement>, bankId: string) {
    event.preventDefault(); setBusy(true); setError("");
    const values = new FormData(event.currentTarget);
    const source_job_id = String(values.get("source_job_id") || "").trim() || undefined;
    const question_ids = String(values.get("question_ids") || "").split(/[\s,]+/).filter(Boolean);
    try { await api.publishBank(bankId, { source_job_id, question_ids }); event.currentTarget.reset(); await load(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  async function manageTeacher(teacher: TeacherAccount, action: "status" | "code") {
    const access_code = action === "code" ? window.prompt(`New five-digit code for ${teacher.name}`) : null;
    if (action === "code" && !access_code) return;
    if (action === "code" && !/^\d{5}$/.test(access_code || "")) { setError("Access codes must be five digits."); return; }
    setBusy(true); setError("");
    try { await api.updateTeacher(teacher.id, action === "code" ? { access_code } : { status: teacher.status === "active" ? "suspended" : "active" }); await refreshTeachers(); }
    catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  async function removeTeacher(teacher: TeacherAccount) {
    if (!window.confirm(`Remove ${teacher.name}? Their classes, tests, and records will be kept, but they will lose access immediately.`)) return;
    setBusy(true); setError(""); setNotice("");
    try { await api.deleteTeacher(teacher.id); await refreshTeachers(); setNotice(`${teacher.name} removed.`); }
    catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  return <section className={s.page}><PageHeader eyebrow="Super admin" title="People & global banks" description="Review source questions, then publish them for teachers." />
    {error && <p className={s.error} role="alert">{error}</p>}
    {notice && <p className={s.notice} role="status">{notice}</p>}
    <div className={s.grid}><section className={s.card}><h2>Teachers</h2><form onSubmit={createTeacher} className={`${s.form} ${s.teacherForm}`}><Field label="Name"><Input name="name" required /></Field><Field label="Email"><Input name="email" type="email" required /></Field><Field label="Five-digit access code"><Input name="access_code" inputMode="numeric" pattern="[0-9]{5}" required /></Field><Button tone="primary" type="submit" disabled={busy}>{busy ? "Saving…" : "Add teacher"}</Button></form><div className={s.list}>{teachers.map(teacher => <div className={s.bank} key={teacher.id}><div className={s.teacherHeading}><strong>{teacher.name}</strong><span className={teacher.status === "active" ? s.activeStatus : s.suspendedStatus}>{teacher.status}</span></div><p className={s.teacherEmail}>{teacher.email}</p><div className={s.teacherMetrics}><span><strong>{teacher.metrics.papers_generated}</strong> generated papers</span><span><strong>{teacher.metrics.tests_published}</strong> published tests</span><span><strong>{teacher.metrics.classes_created}</strong> classes</span><span><strong>{teacher.metrics.students_enrolled}</strong> students</span></div><p className={s.muted}>Total: {teacher.metrics.papers_created} papers · {teacher.metrics.tests_created} tests</p><div className={s.teacherActions}><Button size="small" disabled={busy} onClick={() => void manageTeacher(teacher,"status")}>{teacher.status === "active" ? "Suspend" : "Resume"}</Button><Button size="small" disabled={busy} onClick={() => void manageTeacher(teacher,"code")}>Reset code</Button><Button size="small" disabled={busy} onClick={() => void removeTeacher(teacher)}>Remove</Button></div></div>)}{teachers.length === 0 && <p className={s.muted}>No teachers yet.</p>}</div></section>
    <section className={s.card}><h2>Global question banks</h2><form onSubmit={createBank} className={s.row}><Input name="name" placeholder="Bank name" required /><Button tone="primary" type="submit" disabled={busy}>Create bank</Button></form><p className={s.muted}>Review questions in <Link href="/ingestion">Ingestion</Link>, then publish a completed source or selected question IDs.</p><div className={s.list}>{banks.map(bank => <div className={s.bank} key={bank.id}><div className={s.listItem}><strong>{bank.name}</strong><span>{bank.status} · {bank.question_count} published</span></div><form className={s.form} onSubmit={event => void publish(event, bank.id)}><Select name="source_job_id" defaultValue=""><option value="">Choose a completed source</option>{sources.map(source => <option key={source.id} value={source.id}>{source.source_name}</option>)}</Select><Input name="question_ids" placeholder="Or question IDs, comma separated" /><Button size="small" type="submit" disabled={busy}>Publish reviewed questions</Button></form></div>)}</div></section></div>
  </section>;
}

export function TestsScreen() {
  const [tests, setTests] = useState<CbtTest[]>([]);
  const [classes, setClasses] = useState<Classroom[]>([]);
  const [selected, setSelected] = useState<CbtTest | null>(null);
  const [analytics, setAnalytics] = useState<{ summary: { assigned: number; started: number; submitted: number }; students: Array<{ student: SessionAccount; status: string; score: number | null; attempt_count: number }> } | null>(null);
  const [classIds, setClassIds] = useState<string[]>([]);
  const [studentIds, setStudentIds] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { const [a,b] = await Promise.all([api.tests(),api.classes()]); setTests(a.items); setClasses(b.items); }, []);
  useEffect(() => { void load().catch(e => setError(message(e))); }, [load]);
  async function open(test: CbtTest) { try { const [detail, results] = await Promise.all([api.test(test.id), api.testAnalytics(test.id)]); setSelected(detail); setAnalytics(results); setClassIds(detail.assignments?.map(a => a.class_id).filter((id): id is string => !!id) || []); setStudentIds(detail.assignments?.map(a => a.student_id).filter((id): id is string => !!id) || []); } catch (e) { setError(message(e)); } }
  async function createClass(event: React.FormEvent<HTMLFormElement>) { event.preventDefault(); setBusy(true); const form = event.currentTarget; try { await api.createClass(String(new FormData(form).get("name"))); form.reset(); await load(); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  async function createStudent(event: React.FormEvent<HTMLFormElement>, classId: string) { event.preventDefault(); setBusy(true); const form = event.currentTarget; try { await api.createStudent(classId,Object.fromEntries(new FormData(form))); form.reset(); await load(); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  async function manageStudent(student: SessionAccount, action: "status" | "code") {
    const access_code = action === "code" ? window.prompt(`New five-digit code for ${student.name}`) : null;
    if (action === "code" && !access_code) return;
    if (action === "code" && !/^\d{5}$/.test(access_code || "")) { setError("Access codes must be five digits."); return; }
    setBusy(true); setError("");
    try { await api.updateStudent(student.id, action === "code" ? { access_code } : { status: student.status === "active" ? "suspended" : "active" }); await load(); }
    catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  async function saveAssignments() { if (!selected) return; setBusy(true); try { await api.assignTest(selected.id,{ class_ids: classIds, student_ids: studentIds }); await open(selected); await load(); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  return <section className={s.page}><PageHeader eyebrow="Assessment studio" title="Tests & classes" description="Publish a curated paper as a test, then assign it to a class or student." />{error && <p className={s.error} role="alert">{error}</p>}
    <div className={s.grid}><section className={s.card}><h2>Published tests</h2>{tests.length ? <div className={s.list}>{tests.map(test => <button type="button" className={s.listButton} key={test.id} onClick={() => void open(test)}><strong>{test.title}</strong><span>{test.question_count} questions · Version {test.version} · {test.assignment_count} assignments</span></button>)}</div> : <p className={s.muted}>No tests yet. Open a paper and select “Publish as CBT test”.</p>}
      {selected && <div className={s.detail}><h3>{selected.title}</h3><p>{selected.question_count} questions · {Math.round(selected.duration_seconds/60)} minutes · opens {date(selected.opens_at)} · closes {date(selected.closes_at)}</p><h4>Assign to classes</h4>{classes.map(item => <label className={s.check} key={item.id}><input type="checkbox" checked={classIds.includes(item.id)} onChange={event => setClassIds(event.target.checked ? [...classIds,item.id] : classIds.filter(id => id !== item.id))} />{item.name}</label>)}<h4>Or individual students</h4>{classes.flatMap(item => item.students).filter((student,index,all) => all.findIndex(candidate => candidate.id === student.id) === index).map(student => <label className={s.check} key={student.id}><input type="checkbox" checked={studentIds.includes(student.id)} onChange={event => setStudentIds(event.target.checked ? [...studentIds,student.id] : studentIds.filter(id => id !== student.id))} />{student.name} · {student.email}</label>)}<Button tone="primary" disabled={busy} onClick={() => void saveAssignments()}>Save assignments</Button><h4>Progress</h4><p>{analytics?.summary.started || 0} started · {analytics?.summary.submitted || 0} submitted · {analytics?.summary.assigned || 0} assigned</p>{analytics?.students.map(row => <div className={s.listItem} key={row.student.id}><strong>{row.student.name}</strong><span>{row.status} · {row.score ?? "—"} points</span></div>)}</div>}</section>
    <section className={s.card}><h2>Classes</h2><form className={s.row} onSubmit={createClass}><Input name="name" placeholder="New class name" required /><Button type="submit" disabled={busy}>Create class</Button></form>{classes.map(item => <div className={s.bank} key={item.id}><h3>{item.name}</h3><p className={s.muted}>{item.students.length} students</p>{item.students.map(student => <div className={s.bank} key={student.id}><div className={s.listItem}><strong>{student.name}</strong><span>{student.email} · {student.status}</span></div><div className={s.row}><Button size="small" disabled={busy} onClick={() => void manageStudent(student,"status")}>{student.status === "active" ? "Suspend" : "Reactivate"}</Button><Button size="small" disabled={busy} onClick={() => void manageStudent(student,"code")}>Reset code</Button></div></div>)}<form className={s.form} onSubmit={event => void createStudent(event,item.id)}><h4>Add student</h4><Input name="name" placeholder="Name" required /><Input name="email" type="email" placeholder="Email" required /><Input name="access_code" inputMode="numeric" pattern="[0-9]{5}" placeholder="Five-digit code" required /><Button size="small" type="submit" disabled={busy}>Add student</Button></form></div>)}</section></div>
  </section>;
}

export function StudentTestsScreen() {
  const router = useRouter();
  const [tests, setTests] = useState<CbtTest[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => { void api.studentTests().then(data => setTests(data.items)).catch(e => setError(message(e))); }, []);
  async function start(test: CbtTest) { setBusy(test.id); try { const attempt = await api.startAttempt(test.id); router.push(`/student/attempts/${attempt.id}`); } catch (e) { setError(message(e)); setBusy(null); } }
  return <section className={s.page}><PageHeader eyebrow="Student portal" title="My tests" description="Your assigned assessments and results." />{error && <p className={s.error} role="alert">{error}</p>}<div className={s.grid}>{tests.map(test => <article className={s.card} key={test.id}><h2>{test.title}</h2><p>{test.question_count} questions · {Math.round(test.duration_seconds/60)} minutes</p><p className={s.muted}>{test.availability} · {test.attempts_remaining} attempts remaining</p><div className={s.actions}>{test.latest_attempt?.submitted_at && <Link className={s.linkButton} href={`/student/results/${test.latest_attempt.id}`}>View result</Link>}{test.latest_attempt && !test.latest_attempt.submitted_at ? <Link className={s.linkButton} href={`/student/attempts/${test.latest_attempt.id}`}>Resume attempt</Link> : <Button tone="primary" disabled={test.availability !== "available" || !test.attempts_remaining || busy === test.id} onClick={() => void start(test)}>{test.latest_attempt ? "Start another attempt" : "Start test"}</Button>}</div></article>)}</div>{!tests.length && !error && <p className={s.muted}>No tests are assigned yet.</p>}</section>;
}

function ResponseEditor({ question, value, onChange }: { question: CbtQuestion; value: unknown; onChange: (value: unknown) => void }) {
  const spec = question.response_spec;
  if (spec.mode === "numeric") return <Field label="Numeric answer"><Input type="number" step="any" value={typeof value === "string" || typeof value === "number" ? value : ""} onChange={event => onChange(event.target.value)} /></Field>;
  if (spec.mode === "matching") return spec.answer_combinations?.length ? <Field label="Answer combination"><Select value={typeof value === "string" ? value : ""} onChange={event => onChange(event.target.value)}><option value="">Choose</option>{spec.answer_combinations.map((item,index) => <option key={index} value={String.fromCharCode(65+index)}>{String.fromCharCode(65+index)}. {item}</option>)}</Select></Field> : <div>{spec.list_i?.map(item => <Field key={item} label={item}><Select value={(value && typeof value === "object" ? (value as Record<string,string>)[item] : "") || ""} onChange={event => onChange({ ...(value && typeof value === "object" ? value as object : {}), [item]: event.target.value })}><option value="">Choose</option>{spec.list_ii?.map(option => <option key={option} value={option}>{option}</option>)}</Select></Field>)}</div>;
  const selected = Array.isArray(value) ? value as string[] : typeof value === "string" ? [value] : [];
  return <div className={s.options}>{question.options.map((option,index) => { const letter = String.fromCharCode(65+index); const multi = (spec.max_selections || 1) > 1; return <label key={letter} className={s.option}><input type={multi ? "checkbox" : "radio"} name={`question-${question.id}`} checked={selected.includes(letter)} onChange={event => onChange(multi ? event.target.checked ? [...selected,letter] : selected.filter(item => item !== letter) : [letter])} /><span>{letter}.</span><MathContent value={option} />{question.diagrams.filter(diagram => diagram.option_index === index).map(diagram => <img key={diagram.id} src={diagram.url} alt={diagram.description} />)}</label>; })}</div>;
}

export function AttemptScreen({ id }: { id: string }) {
  const router = useRouter();
  const [attempt, setAttempt] = useState<CbtAttempt | null>(null);
  const [index, setIndex] = useState(0);
  const [answer, setAnswer] = useState<unknown>(null);
  const [remaining, setRemaining] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { void api.attempt(id).then(setAttempt).catch(e => setError(message(e))); }, [id]);
  useEffect(() => { if (!attempt) return; const question = attempt.questions[index]; setAnswer(attempt.responses[question?.id]?.answer ?? null); }, [attempt,index]);
  useEffect(() => { if (!attempt) return; const tick = () => setRemaining(Math.max(0,Math.ceil((new Date(attempt.ends_at).getTime()-Date.now())/1000))); tick(); const timer = window.setInterval(tick,1000); return () => window.clearInterval(timer); }, [attempt]);
  async function save(marked = false): Promise<boolean> { if (!attempt) return false; setBusy(true); setError(""); try { const question = attempt.questions[index]; const result = await api.saveAttemptAnswer(attempt.id,question.id,{ answer, marked_for_review: marked, revision: attempt.revision }); setAttempt({ ...attempt, revision: result.revision, responses: { ...attempt.responses, [question.id]: { answer, marked_for_review: marked } } }); return true; } catch (e) { setError(message(e)); const fresh = await api.attempt(id).catch(() => null); if (fresh) setAttempt(fresh); return false; } finally { setBusy(false); } }
  async function go(next: number) { if (!attempt || busy || next === index) return; const current = attempt.responses[attempt.questions[index].id]?.answer ?? null; if (JSON.stringify(current) !== JSON.stringify(answer) && !await save()) return; setIndex(next); }
  async function submit() { if (!attempt || !window.confirm("Submit this test now?")) return; const current = attempt.responses[attempt.questions[index].id]?.answer ?? null; if (JSON.stringify(current) !== JSON.stringify(answer) && !await save()) return; setBusy(true); try { await api.submitAttempt(attempt.id); router.push(`/student/results/${attempt.id}`); } catch (e) { setError(message(e)); setBusy(false); } }
  if (!attempt) return <section className={s.page}>{error || "Opening test…"}</section>;
  if (attempt.submitted_at || remaining === 0) return <section className={s.page}><PageHeader title="Test submitted" description="Your time has ended or your attempt was submitted." /><Link href={`/student/results/${id}`}>View result when released</Link></section>;
  const question = attempt.questions[index];
  return <section className={s.page}><PageHeader eyebrow={`Question ${index+1} of ${attempt.questions.length}`} title="Assessment" description={`${Math.floor(remaining/60)}:${String(remaining%60).padStart(2,"0")} remaining`} />{error && <p className={s.error} role="alert">{error}</p>}<div className={s.card}><p className={s.muted}>{question.section || "General"} · {question.scoring.full} marks</p><MathContent as="div" value={question.stem} />{question.diagrams.filter(diagram => diagram.option_index == null).map(diagram => <img className={s.diagram} key={diagram.id} src={diagram.url} alt={diagram.description} />)}<ResponseEditor question={question} value={answer} onChange={setAnswer} /><div className={s.actions}><Button disabled={busy} onClick={() => void save()}>Save answer</Button><Button disabled={busy} onClick={() => void save(true)}>Mark for review</Button><Button disabled={index===0 || busy} onClick={() => void go(index-1)}>Previous</Button><Button disabled={index===attempt.questions.length-1 || busy} onClick={() => void go(index+1)}>Next</Button><Button tone="primary" disabled={busy} onClick={() => void submit()}>Submit test</Button></div></div><div className={s.numberGrid}>{attempt.questions.map((item,number) => <button key={item.id} className={number===index ? s.activeNumber : attempt.responses[item.id] ? s.savedNumber : ""} onClick={() => void go(number)}>{number+1}</button>)}</div></section>;
}

export function ResultScreen({ id }: { id: string }) {
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.result>> | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { void api.result(id).then(setResult).catch(e => setError(message(e))); }, [id]);
  return <section className={s.page}><PageHeader eyebrow="Test result" title={result ? `${result.attempt.score ?? 0} points` : "Result"} description={result ? `Submitted ${date(result.attempt.submitted_at)}` : error || "Loading…"} /><Link href="/student">← My tests</Link>{result?.questions.map((item,index) => <article className={s.card} key={item.id}><p className={s.muted}>Question {index+1} · {item.points} points</p><MathContent as="div" value={item.stem} /><p>Your answer: {JSON.stringify(item.response) || "Unanswered"}</p><p>Correct answer: {JSON.stringify(item.answer)}</p>{item.solution && <MathContent as="div" value={item.solution} />}</article>)}</section>;
}
