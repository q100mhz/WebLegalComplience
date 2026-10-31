export interface HowToData { platform: string | null; steps: string[] }

export default function HowTo({ h }: { h?: HowToData | null }) {
  if (!h?.steps.length) return null;
  return (
    <>
      <dt>Как исправить{h.platform ? ` на ${h.platform}` : ""}</dt>
      <dd>
        <ol className="howto">
          {h.steps.map((s) => <li key={s}>{s}</li>)}
        </ol>
      </dd>
    </>
  );
}
