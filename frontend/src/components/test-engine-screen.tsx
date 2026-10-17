"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError, api } from "@/lib/api";
import type { CbtAttempt, CbtQuestion, CbtTest, Classroom, GlobalBank, IngestionJob, SessionAccount, TeacherAccount } from "@/lib/types";
import { Button, Field, Input, PageHeader, Select } from "@/components/ui";
import { MathContent } from "@/components/math-content";
import s from "@/styles/test-engine.module.css";

function message(error: unknown) { return error instanceof Error ? error.message : "The request failed."; }
function date(value: string | null | undefined) { return value ? new Date(value).toLocaleString() : "Any time"; }

export function AdminScreen() {
  const [teachers, setTeachers] = useState<TeacherAccount[]>([]);
  const [banks, setBanks] = useState<GlobalBank[]>([]);
  const [sources, setSources] = useState<IngestionJob[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [refreshingBanks, setRefreshingBanks] = useState(false);
  const [health, setHealth] = useState<Awaited<ReturnType<typeof api.assessmentHealth>> | null>(null);
  const [readiness, setReadiness] = useState<Record<string, Awaited<ReturnType<typeof api.bankReadiness>>>>({});
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
  async function refreshBanks() {
    setRefreshingBanks(true); setError("");
    try { setBanks((await api.banks()).items); }
    catch (e) { setError(message(e)); }
    finally { setRefreshingBanks(false); }
  }
  async function checkHealth() { setBusy(true); try { setHealth(await api.assessmentHealth()); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  async function checkReadiness(bankId: string) { setBusy(true); try { const result = await api.bankReadiness(bankId); setReadiness(current => ({ ...current, [bankId]: result })); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
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
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    const form = event.currentTarget;
    const values = new FormData(form);
    const source_job_id = String(values.get("source_job_id") || "").trim() || undefined;
    const question_ids = String(values.get("question_ids") || "").split(/[\s,]+/).filter(Boolean);
    try { const result = await api.publishBank(bankId, { source_job_id, question_ids }); form.reset(); const confirmation = `Published ${result.published_count} question${result.published_count === 1 ? "" : "s"}.`; setNotice(confirmation); try { await load(); } catch { setNotice(`${confirmation} Refresh the page to update the bank list.`); } } catch (e) { setError(message(e)); } finally { setBusy(false); }
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
  return <section className={s.page}><PageHeader eyebrow="Super admin" title="People & global banks" description="Publish source questions for teachers." />
    {error && <p className={s.error} role="alert">{error}</p>}
    {notice && <p className={s.notice} role="status">{notice}</p>}
    <section className={s.card}><h2>Assessment operations</h2><Button size="small" disabled={busy} onClick={() => void checkHealth()}>Check system status</Button>{health && <div><p>Database: {health.database} · {health.active_attempts} active attempts · {health.failed_generation_jobs} failed generation jobs</p><h3>Recent assessment events</h3>{health.recent_events.map((event, index) => <p className={s.muted} key={`${event.resource_id}-${index}`}>{event.action.replaceAll("_", " ")} · {date(event.created_at)}</p>)}</div>}</section>
    <div className={s.grid}><section className={s.card}><h2>Teachers</h2><form onSubmit={createTeacher} className={`${s.form} ${s.teacherForm}`}><Field label="Name"><Input name="name" required /></Field><Field label="Email"><Input name="email" type="email" required /></Field><Field label="Five-digit access code"><Input name="access_code" inputMode="numeric" pattern="[0-9]{5}" required /></Field><Button tone="primary" type="submit" disabled={busy}>{busy ? "Saving…" : "Add teacher"}</Button></form><div className={s.list}>{teachers.map(teacher => <div className={s.bank} key={teacher.id}><div className={s.teacherHeading}><strong>{teacher.name}</strong><span className={teacher.status === "active" ? s.activeStatus : s.suspendedStatus}>{teacher.status}</span></div><p className={s.teacherEmail}>{teacher.email}</p><div className={s.teacherMetrics}><span><strong>{teacher.metrics.papers_generated}</strong> generated papers</span><span><strong>{teacher.metrics.tests_published}</strong> published tests</span><span><strong>{teacher.metrics.classes_created}</strong> classes</span><span><strong>{teacher.metrics.students_enrolled}</strong> students</span></div><p className={s.muted}>Total: {teacher.metrics.papers_created} papers · {teacher.metrics.tests_created} tests</p><div className={s.teacherActions}><Button size="small" disabled={busy} onClick={() => void manageTeacher(teacher,"status")}>{teacher.status === "active" ? "Suspend" : "Resume"}</Button><Button size="small" disabled={busy} onClick={() => void manageTeacher(teacher,"code")}>Reset code</Button><Button size="small" disabled={busy} onClick={() => void removeTeacher(teacher)}>Remove</Button></div></div>)}{teachers.length === 0 && <p className={s.muted}>No teachers yet.</p>}</div></section>
    <section className={s.card}>
      <div className={s.bankTitle}><h2>Global question banks</h2><Button size="small" disabled={refreshingBanks || busy} onClick={() => void refreshBanks()}>{refreshingBanks ? "Refreshing…" : "Refresh tracking"}</Button></div>
      <form onSubmit={createBank} className={s.row}><Input name="name" placeholder="Bank name" required /><Button tone="primary" type="submit" disabled={busy}>Create bank</Button></form>
      <p className={s.muted}>Publish a source or selected questions. Check or map answers in <Link href="/ingestion">Ingestion</Link>.</p>
      <div className={s.list}>{banks.map(bank => <div className={s.bank} key={bank.id}>
        <div className={s.listItem}><strong>{bank.name}</strong><span>{bank.status} · {bank.question_count} published</span></div>
        {bank.question_count > 0 ? <div className={s.bankTracking}>
          <div className={s.bankMetrics} aria-label={`${bank.name} publication summary`}><span><strong>{bank.question_count}</strong> published</span><span><strong>{bank.missing_answer_count}</strong> missing answers</span><span><strong>{bank.missing_solution_count}</strong> missing solutions</span></div>
          <Button size="small" disabled={busy} onClick={() => void checkReadiness(bank.id)}>Check CBT readiness</Button>{readiness[bank.id] && <div><p>{readiness[bank.id].ready} of {readiness[bank.id].total} ready for CBT publication</p>{readiness[bank.id].blocked.map(item => <p className={s.muted} key={item.question_id}>{item.source_reference || item.question_id}: {item.reason}</p>)}</div>}
          <h3>Published sources</h3>
          <div className={s.publishedSources}>{bank.sources.map(source => <div className={s.publishedSource} key={source.source_job_id || source.source_name}>
            <div>{source.source_job_id ? <Link href={`/ingestion/${source.source_job_id}`}>{source.source_name}</Link> : <strong>{source.source_name}</strong>}</div>
            <p>{source.question_count}{source.source_question_count != null ? ` of ${source.source_question_count}` : ""} in this bank · {source.missing_answer_count} missing answers · {source.missing_solution_count} missing solutions</p>
          </div>)}</div>
        </div> : <p className={s.muted}>No questions published in this bank yet.</p>}
        <form className={s.form} onSubmit={event => void publish(event, bank.id)}><Select name="source_job_id" defaultValue=""><option value="">Choose a completed source</option>{sources.map(source => <option key={source.id} value={source.id}>{source.source_name}</option>)}</Select><Input name="question_ids" placeholder="Or question IDs, comma separated" /><p className={s.muted}>Every selected question must have a recorded answer and explicit source-review approval.</p><Button size="small" type="submit" disabled={busy}>Publish questions</Button></form>
      </div>)}</div>
    </section></div>
  </section>;
}

export function TestsScreen() {
  const [tests, setTests] = useState<CbtTest[]>([]);
  const [classes, setClasses] = useState<Classroom[]>([]);
  const [students, setStudents] = useState<SessionAccount[]>([]);
  const [selected, setSelected] = useState<CbtTest | null>(null);
  const [analytics, setAnalytics] = useState<Awaited<ReturnType<typeof api.testAnalytics>> | null>(null);
  const [viewedVersion, setViewedVersion] = useState<Awaited<ReturnType<typeof api.testVersion>> | null>(null);
  const [classIds, setClassIds] = useState<string[]>([]);
  const [studentIds, setStudentIds] = useState<string[]>([]);
  const [secondTest, setSecondTest] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { const [a,b,c] = await Promise.all([api.tests(),api.classes(),api.students()]); setTests(a.items); setClasses(b.items); setStudents(c.items); }, []);
  useEffect(() => { void load().catch(e => setError(message(e))); }, [load]);
  async function open(test: CbtTest) { try { const [detail, results] = await Promise.all([api.test(test.id), api.testAnalytics(test.id)]); if (selected?.id !== test.id) setViewedVersion(null); setSelected(detail); setAnalytics(results); setClassIds(detail.assignments?.map(a => a.class_id).filter((id): id is string => !!id) || []); setStudentIds(detail.assignments?.map(a => a.student_id).filter((id): id is string => !!id) || []); } catch (e) { setError(message(e)); } }
  async function viewVersion(versionId: string) { if (!selected) return; try { setViewedVersion(await api.testVersion(selected.id, versionId)); } catch (e) { setError(message(e)); } }
  async function createClass(event: React.FormEvent<HTMLFormElement>) { event.preventDefault(); setBusy(true); const form = event.currentTarget; try { await api.createClass(String(new FormData(form).get("name"))); form.reset(); await load(); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  async function createStudent(event: React.FormEvent<HTMLFormElement>, classId: string) { event.preventDefault(); setBusy(true); const form = event.currentTarget; try { await api.createStudent(classId,Object.fromEntries(new FormData(form))); form.reset(); await load(); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  async function createStandaloneStudent(event: React.FormEvent<HTMLFormElement>) { event.preventDefault(); setBusy(true); const form = event.currentTarget; try { await api.createStandaloneStudent(Object.fromEntries(new FormData(form))); form.reset(); await load(); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  async function addExisting(event: React.FormEvent<HTMLFormElement>, classId: string) { event.preventDefault(); const form = event.currentTarget; setBusy(true); try { await api.addClassMember(classId, String(new FormData(form).get("email"))); form.reset(); await load(); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  async function removeMember(classId: string, student: SessionAccount) { if (!window.confirm(`Remove ${student.name} from this class? Existing attempts are retained.`)) return; setBusy(true); try { await api.removeClassMember(classId, student.id); await load(); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  async function accommodate(student: SessionAccount) { if (!selected) return; const current = (selected.accommodations?.find(item => item.student_id === student.id)?.extra_seconds || 0) / 60; const input = window.prompt(`Extra minutes for ${student.name}. This applies to new attempts; the closing time still applies.`, String(current)); if (input === null) return; const minutes = Number(input); if (!Number.isInteger(minutes) || minutes < 0 || minutes > 360) { setError("Extra time must be a whole number between 0 and 360 minutes."); return; } setBusy(true); try { await api.setAccommodation(selected.id, student.id, minutes); await open(selected); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  async function manageStudent(student: SessionAccount, action: "status" | "code") {
    const access_code = action === "code" ? window.prompt(`New five-digit code for ${student.name}`) : null;
    if (action === "code" && !access_code) return;
    if (action === "code" && !/^\d{5}$/.test(access_code || "")) { setError("Access codes must be five digits."); return; }
    setBusy(true); setError("");
    try { await api.updateStudent(student.id, action === "code" ? { access_code } : { status: student.status === "active" ? "suspended" : "active" }); await load(); }
    catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  async function changePaperLink() { if (!selected) return; setBusy(true); try { if (selected.linked_pair) await api.unlinkTest(selected.id); else if (secondTest) await api.linkTest(selected.id, secondTest); await open(selected); setSecondTest(""); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  async function saveAssignments() { if (!selected) return; setBusy(true); try { await api.assignTest(selected.id,{ class_ids: classIds, student_ids: studentIds }); await open(selected); await load(); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  return <section className={s.page}><PageHeader eyebrow="Assessment studio" title="Tests & classes" description="Publish a curated paper as a test, then assign it to a class or student." />{error && <p className={s.error} role="alert">{error}</p>}
    <div className={s.grid}><section className={s.card}><h2>Published tests</h2>{tests.length ? <div className={s.list}>{tests.map(test => <button type="button" className={s.listButton} key={test.id} onClick={() => void open(test)}><strong>{test.title}</strong><span>{test.question_count} questions · Version {test.version} · {test.assignment_count} assignments</span></button>)}</div> : <p className={s.muted}>No tests yet. Open a paper and select “Publish as CBT test”.</p>}
      {selected && <div className={s.detail}><h3>{selected.title}</h3><p>{selected.question_count} questions · {Math.round(selected.duration_seconds/60)} minutes · opens {date(selected.opens_at)} · closes {date(selected.closes_at)}</p><h4>Assign to classes</h4>{classes.map(item => <label className={s.check} key={item.id}><input type="checkbox" checked={classIds.includes(item.id)} onChange={event => setClassIds(event.target.checked ? [...classIds,item.id] : classIds.filter(id => id !== item.id))} />{item.name}</label>)}<h4>Or individual students</h4>{students.map(student => <label className={s.check} key={student.id}><input type="checkbox" checked={studentIds.includes(student.id)} onChange={event => setStudentIds(event.target.checked ? [...studentIds,student.id] : studentIds.filter(id => id !== student.id))} />{student.name} · {student.email}</label>)}<Button tone="primary" disabled={busy} onClick={() => void saveAssignments()}>Save assignments</Button><h4>Two-paper mock</h4>{selected.linked_pair ? <p>Linked with {tests.find(test => test.id === (selected.linked_pair?.first_test_id === selected.id ? selected.linked_pair.second_test_id : selected.linked_pair?.first_test_id))?.title || "another paper"}. <Button size="small" disabled={busy} onClick={() => void changePaperLink()}>Unlink</Button></p> : <div className={s.row}><Select aria-label="Second paper" value={secondTest} onChange={event => setSecondTest(event.target.value)}><option value="">Choose another published test</option>{tests.filter(test => test.id !== selected.id).map(test => <option key={test.id} value={test.id}>{test.title}</option>)}</Select><Button size="small" disabled={busy || !secondTest} onClick={() => void changePaperLink()}>Link second paper</Button></div>}<p className={s.muted}>Assign both papers separately. Combined scores appear after both results are released.</p><h4>Published versions</h4><div className={s.row}>{selected.versions?.map(version => <Button key={version.id} size="small" onClick={() => void viewVersion(version.id)}>Version {version.ordinal} · {date(version.published_at)}</Button>)}</div>{viewedVersion && <div className={s.bank}><h4>Version {viewedVersion.ordinal} snapshot</h4>{viewedVersion.content.questions.map((question, index) => <div key={question.id}><p className={s.muted}>Question {index + 1} · {question.section || "General"} · {question.kind}</p><MathContent as="p" value={question.stem} /></div>)}</div>}<h4>Progress</h4><p>{analytics?.summary.started || 0} started · {analytics?.summary.submitted || 0} submitted · {analytics?.summary.assigned || 0} assigned</p>{analytics?.students.map(row => <div className={s.listItem} key={row.student.id}><strong>{row.student.name}</strong><span>{row.status} · {row.score ?? "—"} points · {(selected.accommodations?.find(item => item.student_id === row.student.id)?.extra_seconds || 0) / 60} extra min <Button size="small" disabled={busy} onClick={() => void accommodate(row.student)}>Set extra time</Button></span></div>)}<p><a className={s.linkButton} href={`/api/assessments/tests/${selected.id}/analytics.csv`}>Export student results (CSV)</a></p><h4>Question analysis</h4>{analytics?.questions.length ? analytics.questions.map(question => <div className={s.bank} key={`${question.version}-${question.question_id}`}><p className={s.muted}>Version {question.version} · {question.section || "General"}</p><MathContent as="p" value={question.stem} /><p>{question.correct} of {question.responses} fully correct · average {(question.points_total / question.responses).toFixed(2)} points</p></div>) : <p className={s.muted}>Question analysis appears after students submit.</p>}</div>}</section>
    <section className={s.card}><h2>Students & classes</h2><form className={s.form} onSubmit={createStandaloneStudent}><h3>Add an individual student</h3><p className={s.muted}>Individual students can be assigned directly or added to a class later.</p><Input name="name" placeholder="Name" required /><Input name="email" type="email" placeholder="Email" required /><Input name="access_code" inputMode="numeric" pattern="[0-9]{5}" placeholder="Five-digit code" required /><Button size="small" type="submit" disabled={busy}>Add individual student</Button></form><form className={s.row} onSubmit={createClass}><Input name="name" placeholder="New class name" required /><Button type="submit" disabled={busy}>Create class</Button></form>{classes.map(item => <div className={s.bank} key={item.id}><h3>{item.name}</h3><p className={s.muted}>{item.students.length} students</p>{item.students.map(student => <div className={s.bank} key={student.id}><div className={s.listItem}><strong>{student.name}</strong><span>{student.email} · {student.status}</span></div><div className={s.row}><Button size="small" disabled={busy} onClick={() => void manageStudent(student,"status")}>{student.status === "active" ? "Suspend" : "Reactivate"}</Button><Button size="small" disabled={busy} onClick={() => void manageStudent(student,"code")}>Reset code</Button><Button size="small" disabled={busy} onClick={() => void removeMember(item.id, student)}>Remove from class</Button></div></div>)}<form className={s.row} onSubmit={event => void addExisting(event, item.id)}><Input name="email" type="email" required placeholder="Existing student email" /><Button size="small" type="submit" disabled={busy}>Add existing student</Button></form><form className={s.form} onSubmit={event => void createStudent(event,item.id)}><h4>Add student to class</h4><Input name="name" placeholder="Name" required /><Input name="email" type="email" placeholder="Email" required /><Input name="access_code" inputMode="numeric" pattern="[0-9]{5}" placeholder="Five-digit code" required /><Button size="small" type="submit" disabled={busy}>Add student</Button></form></div>)}</section></div>
  </section>;
}

export function StudentTestsScreen() {
  const router = useRouter();
  const [tests, setTests] = useState<CbtTest[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => { void api.studentTests().then(data => setTests(data.items)).catch(e => setError(message(e))); }, []);
  async function start(test: CbtTest) { setBusy(test.id); try { const attempt = await api.startAttempt(test.id); router.push(`/student/attempts/${attempt.id}`); } catch (e) { setError(message(e)); setBusy(null); } }
  return <section className={s.page}><PageHeader eyebrow="Student portal" title="My tests" description="Your assigned assessments and results." />{error && <p className={s.error} role="alert">{error}</p>}<div className={s.grid}>{tests.map(test => {
    const history = test.attempts || (test.latest_attempt ? [test.latest_attempt] : []);
    const active = test.active_attempt ?? history.find(attempt => !attempt.submitted_at) ?? null;
    const submitted = history.filter(attempt => attempt.submitted_at);
    return <article className={s.card} key={test.id}><h2>{test.title}</h2><p>{test.question_count} questions · {Math.round(test.duration_seconds/60)} minutes</p><p className={s.muted}>{test.availability} · {test.attempts_remaining} attempts remaining</p>{test.linked_summary && <p>Combined practice score ({test.linked_summary.first_title} + {test.linked_summary.second_title}): {test.linked_summary.combined_score} points</p>}<div className={s.actions}>{active ? <Link className={s.linkButton} href={`/student/attempts/${active.id}`}>Resume attempt</Link> : <Button tone="primary" disabled={test.availability !== "available" || !test.attempts_remaining || busy === test.id} onClick={() => void start(test)}>{submitted.length ? "Start another attempt" : "Start test"}</Button>}</div>{submitted.length > 0 && <div className={s.attemptHistory}><h3>Attempt history</h3>{submitted.map((attempt, index) => <div className={s.listItem} key={attempt.id}><span>Attempt {submitted.length - index} · {date(attempt.submitted_at)}</span>{attempt.score === null ? <strong>Result pending release</strong> : <Link href={`/student/results/${attempt.id}`}>View result · {attempt.score} points</Link>}</div>)}</div>}</article>;
  })}</div>{!tests.length && !error && <p className={s.muted}>No tests are assigned yet.</p>}</section>;
}

function ResponseEditor({ question, value, onChange }: { question: CbtQuestion; value: unknown; onChange: (value: unknown) => void }) {
  const spec = question.response_spec;
  if (spec.mode === "numeric") return <Field label="Numeric answer"><Input type="number" step="any" value={typeof value === "string" || typeof value === "number" ? value : ""} onChange={event => onChange(event.target.value)} /></Field>;
  if (spec.mode === "matching") return spec.answer_combinations?.length ? <Field label="Answer combination"><Select value={typeof value === "string" ? value : ""} onChange={event => onChange(event.target.value)}><option value="">Choose</option>{spec.answer_combinations.map((item,index) => <option key={index} value={String.fromCharCode(65+index)}>{String.fromCharCode(65+index)}. {item}</option>)}</Select></Field> : <div>{spec.list_i?.map(item => <Field key={item} label={item}><Select value={(value && typeof value === "object" ? (value as Record<string,string>)[item] : "") || ""} onChange={event => { const next = { ...(value && typeof value === "object" ? value as Record<string, string> : {}) }; if (event.target.value) next[item] = event.target.value; else delete next[item]; onChange(next); }}><option value="">Choose</option>{spec.list_ii?.map(option => <option key={option} value={option}>{option}</option>)}</Select></Field>)}</div>;
  const selected = Array.isArray(value) ? value as string[] : typeof value === "string" ? [value] : [];
  return <div className={s.options}>{question.options.map((option,index) => { const letter = String.fromCharCode(65+index); const multi = (spec.max_selections || 1) > 1; return <label key={letter} className={s.option}><input type={multi ? "checkbox" : "radio"} name={`question-${question.id}`} checked={selected.includes(letter)} onChange={event => onChange(multi ? event.target.checked ? [...selected,letter] : selected.filter(item => item !== letter) : [letter])} /><span>{letter}.</span><MathContent value={option} />{question.diagrams.filter(diagram => diagram.option_index === index).map(diagram => <img key={diagram.id} src={diagram.url} alt={diagram.description} />)}</label>; })}</div>;
}

export function AttemptScreen({ id }: { id: string }) {
  const router = useRouter();
  const [attempt, setAttempt] = useState<CbtAttempt | null>(null);
  const [index, setIndex] = useState(0);
  const [answer, setAnswer] = useState<unknown>(null);
  const [marked, setMarked] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [visited, setVisited] = useState<string[]>([]);
  const [remaining, setRemaining] = useState<number | null>(null);
  const [clockOffset, setClockOffset] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const currentId = attempt?.questions[index]?.id;
  const attemptExists = Boolean(attempt);
  const attemptSubmitted = Boolean(attempt?.submitted_at);
  const storageKey = `cbt-draft:${id}:${currentId}`;
  const confirmTimeout = useCallback(async () => {
    try {
      const latest = await api.attempt(id);
      setAttempt(latest);
      if (latest.server_time) setClockOffset(new Date(latest.server_time).getTime() - Date.now());
      setError("");
    } catch {
      setError("Could not confirm submission. Reconnect and check again; saved answers will be submitted when the server is reached.");
    }
  }, [id]);

  useEffect(() => { void api.attempt(id).then(data => { setAttempt(data); if (data.server_time) setClockOffset(new Date(data.server_time).getTime() - Date.now()); }).catch(e => setError(message(e))); }, [id]);
  useEffect(() => {
    if (!attempt || !currentId) return;
    const saved = attempt.responses[currentId];
    try {
      const pending = sessionStorage.getItem(storageKey);
      if (pending) { const draft = JSON.parse(pending); setAnswer(draft.answer); setMarked(draft.marked); setDirty(true); if (draft.revision !== undefined && draft.revision !== attempt.revision) setError("This attempt changed in another session. Review your recovered answer and save it again to replace the server version."); }
      else { setAnswer(saved?.answer ?? null); setMarked(saved?.marked_for_review ?? false); setDirty(false); }
    } catch { setAnswer(saved?.answer ?? null); setMarked(saved?.marked_for_review ?? false); setDirty(false); }
    setVisited(previous => previous.includes(currentId) ? previous : [...previous, currentId]);
    // Load only when navigating; refreshes after save must not overwrite a draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId, storageKey]);
  useEffect(() => { if (!attempt) return; const tick = () => setRemaining(Math.max(0, Math.ceil((new Date(attempt.ends_at).getTime() - (Date.now() + clockOffset)) / 1000))); tick(); const timer = window.setInterval(tick, 1000); return () => window.clearInterval(timer); }, [attempt, clockOffset]);
  useEffect(() => { if (remaining === 0 && attemptExists && !attemptSubmitted) void confirmTimeout(); }, [remaining, attemptExists, attemptSubmitted, confirmTimeout]);

  function change(nextAnswer: unknown, nextMarked = marked) {
    setAnswer(nextAnswer); setMarked(nextMarked); setDirty(true); setError("");
    try { sessionStorage.setItem(storageKey, JSON.stringify({ answer: nextAnswer, marked: nextMarked, revision: attempt?.revision })); } catch { /* Keep the in-memory draft when storage is unavailable. */ }
  }
  async function save(nextAnswer = answer, nextMarked = marked): Promise<boolean> {
    if (!attempt || !currentId || busy) return false;
    setBusy(true); setError("");
    try {
      const result = await api.saveAttemptAnswer(attempt.id, currentId, { answer: nextAnswer, marked_for_review: nextMarked, revision: attempt.revision });
      setAttempt({ ...attempt, revision: result.revision, responses: { ...attempt.responses, [currentId]: { answer: nextAnswer, marked_for_review: nextMarked } } });
      setDirty(false); try { sessionStorage.removeItem(storageKey); } catch { /* Storage may be unavailable. */ }
      return true;
    } catch (e) {
      setError(`${message(e)} Your answer is still on this screen; retry saving before leaving.`);
      if (e instanceof ApiError && e.status === 409) {
        const fresh = await api.attempt(id).catch(() => null);
        if (fresh) { setAttempt(fresh); if (fresh.server_time) setClockOffset(new Date(fresh.server_time).getTime() - Date.now()); }
      }
      return false;
    } finally { setBusy(false); }
  }
  useEffect(() => {
    if (!dirty || busy || error || !attempt || attempt.submitted_at || remaining === 0) return;
    const timer = window.setTimeout(() => void save(), 1200);
    return () => window.clearTimeout(timer);
    // Save the current draft after the student stops typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [answer, marked, dirty, busy, error, currentId]);
  async function go(next: number) { if (!attempt || busy || next === index) return; if (dirty && !await save()) return; setIndex(next); }
  async function submit() {
    if (!attempt || busy) return;
    const prospectiveResponses = currentId ? { ...attempt.responses, [currentId]: { answer, marked_for_review: marked } } : attempt.responses;
    const answered = Object.values(prospectiveResponses).filter(row => row.answer !== null && row.answer !== "" && !(Array.isArray(row.answer) && !row.answer.length)).length;
    if (!window.confirm(`Submit this test? ${answered} of ${attempt.questions.length} questions have answers.`)) return;
    if (dirty && !await save()) return;
    setBusy(true); try { await api.submitAttempt(attempt.id); router.push(`/student/results/${attempt.id}`); } catch (e) { setError(message(e)); setBusy(false); }
  }
  if (!attempt) return <section className={s.page}>{error || "Opening test…"}</section>;
  if (attempt.submitted_at || remaining === 0) return <section className={s.page}><PageHeader title="Test ended" description={attempt.submitted_at ? "Your saved answers have been submitted." : "Checking submission with the server…"} />{error && <p className={s.error} role="alert">{error}</p>}{!attempt.submitted_at && error && <Button onClick={() => void confirmTimeout()}>Check submission</Button>}<Link href={`/student/results/${id}`}>View result when released</Link>{dirty && <p className={s.error}>Your last unsaved answer could not be submitted before the deadline.</p>}</section>;
  const question = attempt.questions[index];
  const sections = [...new Set(attempt.questions.map(item => item.section || "General"))];
  return <section className={`${s.page} ${s.attemptPage}`}>
    <header className={s.examHeader}><div><span>Question {index + 1} of {attempt.questions.length}</span><h1>Assessment</h1></div><div className={s.examStatus}><small>{dirty ? "Unsaved changes" : busy ? "Saving…" : "Saved"}</small><strong aria-label="Time remaining">{remaining === null ? "Synchronizing…" : `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`}</strong><button className={s.paletteToggle} type="button" aria-expanded={paletteOpen} aria-controls="question-palette" onClick={() => setPaletteOpen(true)}>Questions</button></div></header>
    {error && <p className={s.error} role="alert">{error}</p>}
    <nav className={s.actions} aria-label="Test sections">{sections.map(section => <Button key={section} size="small" disabled={busy} onClick={() => void go(attempt.questions.findIndex(item => (item.section || "General") === section))}>{section}</Button>)}</nav>
    <div className={s.attemptGrid}>
      <main className={`${s.card} ${s.questionCard}`}><p className={s.muted}>{question.section || "General"} · {question.scoring.full} marks</p>
        {question.response_spec.instructions && <p>{question.response_spec.instructions}</p>}
        <MathContent as="div" value={question.stem} />{question.diagrams.filter(diagram => diagram.option_index == null).map(diagram => <img className={s.diagram} key={diagram.id} src={diagram.url} alt={diagram.description} />)}
        <fieldset disabled={busy} style={{ border: 0, padding: 0 }}><ResponseEditor question={question} value={answer} onChange={value => change(value)} /></fieldset>
        <div className={`${s.actions} ${s.attemptActions}`}><Button disabled={busy} onClick={() => void save()}>Save answer</Button><Button disabled={busy} onClick={() => change(null)}>Clear response</Button><Button disabled={busy} onClick={() => change(answer, !marked)}>{marked ? "Remove review mark" : "Mark for review"}</Button><Button disabled={index === 0 || busy} onClick={() => void go(index - 1)}>Previous</Button><Button disabled={index === attempt.questions.length - 1 || busy} onClick={() => void go(index + 1)}>Next</Button><Button tone="primary" disabled={busy} onClick={() => void submit()}>Submit test</Button></div>
      </main>
      {paletteOpen && <button className={s.paletteBackdrop} type="button" aria-label="Close question palette" onClick={() => setPaletteOpen(false)} />}
      <aside id="question-palette" className={`${s.palettePanel} ${paletteOpen ? s.palettePanelOpen : ""}`}><div className={s.paletteHeading}><div><strong>Questions</strong><small>{Object.keys(attempt.responses).length} saved responses</small></div><button type="button" onClick={() => setPaletteOpen(false)}>Close</button></div><p className={s.muted}>Not visited · unanswered · answered · marked for review</p><div className={s.numberGrid} aria-label="Question palette">{attempt.questions.map((item, number) => {
        const response = number === index ? { answer, marked_for_review: marked } : attempt.responses[item.id];
        const hasAnswer = response?.answer !== null && response?.answer !== undefined && response?.answer !== "" && !(Array.isArray(response.answer) && !response.answer.length);
        const status = response?.marked_for_review ? hasAnswer ? "Answered and marked for review" : "Marked for review" : hasAnswer ? "Answered" : visited.includes(item.id) || response ? "Unanswered" : "Not visited";
        return <button key={item.id} className={number === index ? s.activeNumber : ""} data-status={status} aria-label={`Question ${number + 1}: ${status}`} title={status} aria-current={number === index ? "true" : undefined} onClick={() => { setPaletteOpen(false); void go(number); }}>{number + 1}<span className={s.paletteStatus}>{status}</span></button>;
      })}</div></aside>
    </div>
  </section>;
}

export function ResultScreen({ id }: { id: string }) {
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.result>> | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { void api.result(id).then(setResult).catch(e => setError(message(e))); }, [id]);
  const displayAnswer = (value: unknown): string => value == null || value === "" ? "Unanswered" : Array.isArray(value) ? value.join(", ") : typeof value === "object" ? Object.entries(value as Record<string, unknown>).map(([key, answer]) => `${key}: ${String(answer)}`).join(", ") : String(value);
  const sections = new Map<string, { points: number; maximum: number }>();
  result?.questions.forEach(item => { const name = item.section || "General"; const current = sections.get(name) || { points: 0, maximum: 0 }; sections.set(name, { points: current.points + item.points, maximum: current.maximum + item.scoring.full }); });
  return <section className={s.page}><PageHeader eyebrow="Test result" title={result ? `${result.attempt.score ?? 0} points` : "Result"} description={result ? `Submitted ${date(result.attempt.submitted_at)}` : error || "Loading…"} /><Link href="/student">← My tests</Link>{result && <section className={s.card}><h2>Section scores</h2>{[...sections].map(([section, score]) => <p key={section}>{section}: {score.points} / {score.maximum}</p>)}</section>}{result?.questions.map((item,index) => <article className={s.card} key={item.id}><p className={s.muted}>Question {index+1} · {item.points} points</p><MathContent as="div" value={item.stem} />{item.diagrams.filter(diagram => diagram.option_index == null).map(diagram => <img key={diagram.id} className={s.diagram} src={diagram.url} alt={diagram.description} />)}{item.options.map((option, optionIndex) => <p key={optionIndex}>{String.fromCharCode(65 + optionIndex)}. <MathContent value={option} /></p>)}<p>Your answer: {displayAnswer(item.response)}</p><p>Correct answer: {displayAnswer(item.answer)}</p>{item.solution && <MathContent as="div" value={item.solution} />}</article>)}</section>;
}
