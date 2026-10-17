import { AttemptScreen } from "@/components/test-engine-screen";
export default async function Page({ params }: { params: Promise<{ id: string }> }) { const { id } = await params; return <AttemptScreen id={id} />; }
