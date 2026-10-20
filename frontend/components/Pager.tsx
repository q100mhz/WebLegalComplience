export default function Pager({ page, total, onChange, per = 50 }: { page: number; total: number; onChange: (p: number) => void; per?: number }) {
  const pages = Math.ceil(total / per);
  if (pages <= 1) return null;
  return (
    <div className="pager">
      <button className="btn secondary small" disabled={page <= 1} onClick={() => onChange(page - 1)}>←</button>
      <span>{page} / {pages}</span>
      <button className="btn secondary small" disabled={page >= pages} onClick={() => onChange(page + 1)}>→</button>
    </div>
  );
}
