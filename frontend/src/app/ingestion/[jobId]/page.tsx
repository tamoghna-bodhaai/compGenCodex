import { SourceReviewScreen } from "@/components/source-review-screen";

export default async function SourceReviewPage({ params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = await params;
  return <SourceReviewScreen jobId={jobId} />;
}
