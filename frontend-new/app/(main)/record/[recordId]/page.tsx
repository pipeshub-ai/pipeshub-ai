import { RecordViewShell } from './components/record-view-shell';

export default async function RecordPage({ params }: { params: Promise<{ recordId: string }> }) {
  const { recordId } = await params;
  return <RecordViewShell recordId={recordId} />;
}
