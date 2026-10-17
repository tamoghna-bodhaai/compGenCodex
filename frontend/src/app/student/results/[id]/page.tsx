import { ResultScreen } from "@/components/test-engine-screen";
export default async function Page({ params }: { params: Promise<{ id: string }> }) { const { id } = await params; return <ResultScreen id={id} />; }
