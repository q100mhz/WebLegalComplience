"""Выгрузка начального реестра нарушений в Markdown для юридической сверки.

    cd backend && python -m scripts.export_registry > ../docs/registry.md
"""

from collections import defaultdict

from app.registry import RULES

CONF = {"high": "🟢 высокая", "medium": "🟡 средняя", "low": "🔴 низкая — проверить обязательно"}


def cell(v: str) -> str:
    return (v or "—").replace("|", "/")


def main() -> None:
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in RULES:
        by_cat[r["category"]].append(r)
    print("# Реестр нарушений для сверки\n")
    print("Начальные данные реестра, которые сервис использует в отчётах. Суммы — по памяти составителя, ")
    print("**каждую строку нужно сверить с действующей редакцией КоАП РФ**. После сверки внесите правки ")
    print("в админке (раздел «Реестр нарушений») и отметьте правило как «Сверено» — отчёты сразу начнут ")
    print("показывать новые суммы. Этот файл после этого не обновляется автоматически.\n")
    print(f"Всего правил: {len(RULES)}. Уверенность: " + ", ".join(
        f"{v} — {sum(1 for r in RULES if r['confidence'] == k)}" for k, v in CONF.items()) + ".\n")
    n = 0
    for cat, rules in by_cat.items():
        print(f"## {cat}\n")
        for r in rules:
            n += 1
            print(f"### {n}. {r['title']}\n")
            print(f"- **Код правила:** `{r['id']}`")
            print(f"- **Норма закона:** {r['law']}")
            print(f"- **Состав КоАП:** {cell(r['koap'])}")
            print(f"- **Уверенность в суммах:** {CONF[r['confidence']]}")
            if r["note"]:
                print(f"- **Что проверить:** {r['note']}")
            print()
            print("| Граждане | Должностные лица | ИП | Юрлица | Повторно |")
            print("|---|---|---|---|---|")
            print("| " + " | ".join(cell(r[k]) + (" ₽" if r[k] and r[k][0].isdigit() else "")
                                    for k in ("fine_citizen", "fine_official", "fine_ip", "fine_legal", "fine_repeat")) + " |")
            print()


if __name__ == "__main__":
    main()
