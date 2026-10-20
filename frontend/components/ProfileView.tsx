import type { Profile } from "@/lib/types";

export default function ProfileView({ profile }: { profile: Profile }) {
  const forms = profile.forms.filter((f) => f.kind !== "search");
  const features = Object.values(profile.features);
  return (
    <section className="profile">
      <h2>Что мы узнали о сайте</h2>
      <div className="profile-grid">
        <div className="card">
          <div className="k">Тип сайта</div>
          <div className="v">{profile.primary_type}</div>
          {profile.types.length > 1 && (
            <div className="sub">также: {profile.types.slice(1).map((t) => t.name).join(", ")}</div>
          )}
        </div>
        <div className="card">
          <div className="k">Платформа</div>
          <div className="v">
            {profile.platform ? profile.platform.name : "Не определена"}
            {profile.platform?.foreign && <span className="pill fail">иностранная</span>}
          </div>
        </div>
        <div className="card">
          <div className="k">Формы сбора данных</div>
          <div className="v">{forms.filter((f) => f.pd).length}</div>
          <div className="sub">всего форм: {forms.length}</div>
        </div>
        <div className="card">
          <div className="k">Онлайн-оплата</div>
          <div className="v">{profile.payments.length ? profile.payments.join(", ") : "Не обнаружена"}</div>
        </div>
      </div>

      {features.length > 0 && (
        <div className="tags">
          {features.map((f) => (
            <span key={f} className="tag">
              {f}
            </span>
          ))}
          {profile.chat.map((c) => (
            <span key={c} className="tag">
              Чат: {c}
            </span>
          ))}
        </div>
      )}

      {forms.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Назначение формы</th>
                <th>Поля</th>
                <th>Согласие на ПДн</th>
                <th>Страница</th>
              </tr>
            </thead>
            <tbody>
              {forms.slice(0, 12).map((f, i) => (
                <tr key={i}>
                  <td>
                    {f.kind_name}
                    {f.special && <span className="pill warn">чувствительные данные</span>}
                    {f.card && <span className="pill warn">данные карты</span>}
                  </td>
                  <td>{f.fields.join(", ")}</td>
                  <td>{!f.pd ? "—" : f.consent_checkbox ? <span className="txt-ok">есть</span> : <span className="txt-bad">нет</span>}</td>
                  <td className="url">{f.page.replace(/^https?:\/\//, "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
