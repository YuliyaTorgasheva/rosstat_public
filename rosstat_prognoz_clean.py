import json
from pathlib import Path
from typing import Any

import pandas as pd


"""
Упрощённый скрипт для интервью.

Задача: взять исходную таблицу Rosstat (Progn_3a.xls), где данные идут блоками
по годам, и превратить её в "плоский" датасет (year × age) + сохранить сноски.

Что здесь намеренно НЕ сделано (кандидат может предложить улучшения):
- обработка ошибок (нет файла, битый XLS, нет pyarrow для Parquet, права на запись)
- валидация данных (ожидаемые колонки/типы, пропуски year, контроль диапазонов)
- контроль изменений источника (хэш/размер/mtime файла, журнал запусков, версионирование)
"""

# Все пути делаем относительными к папке `public_test`, чтобы скрипт работал
# независимо от текущей директории запуска.
BASE_DIR = Path(__file__).resolve().parent
INPUT_XLS = BASE_DIR / "Progn_3a.xls"
OUTPUT_DIR = BASE_DIR / "indicators"

SOURCE_TYPE = "rsdocs"
THEME_ID = 208
DOC_ID = 10040
INDICATOR_ID = 10001
FORECAST_TYPE = "Средний"

YEAR_MIN = 2024
YEAR_MAX = 2046


def output_stem(source_type: str, theme_id: int, doc_id: int, indicator_id: int) -> str:
    # Используется для имени выходных файлов, чтобы идентификаторы можно было
    # восстановить из названия.
    return f"{source_type}_{theme_id}{doc_id}{indicator_id}"


def is_year_row(text: str, years: set[str]) -> bool:
    # В исходной таблице год обычно отдельной строкой (иногда "2024 год").
    # Здесь используем проверку "подстрока входит", как в ноутбуке.
    return any(year in text for year in years)


def is_footnote_row(text: str, *, min_len: int = 20) -> bool:
    # Сноски внизу: начинаются с цифры (или "1 ", "2 ", ...), и дальше длинный текст.
    if not text:
        return False

    starts_like_number = text[0].isdigit() or text.startswith(("1 ", "2 ", "3 ", "4 "))
    if not starts_like_number:
        return False

    return ("Демографический прогноз составлен без учета" in text) or (len(text) > min_len)


def main() -> int:
    out_stem = output_stem(SOURCE_TYPE, THEME_ID, DOC_ID, INDICATOR_ID)

    # 1) Читаем исходный XLS.
    df = pd.read_excel(str(INPUT_XLS))
    df.columns = ["Возраст", "Всего", "Мужчины", "Женщины"]

    # 2) Добавляем "паспорт" датасета (чтобы можно было однозначно связать с источником).
    df["doc_type"] = SOURCE_TYPE
    df["theme_id"] = THEME_ID
    df["doc_id"] = DOC_ID
    df["indicator_id"] = INDICATOR_ID
    df["Вариант прогноза"] = FORECAST_TYPE

    # 3) Приводим поле "Возраст" к строке и выкидываем "шапку" таблицы/пустые строки.
    df["Возраст"] = df["Возраст"].astype(str).str.strip()
    df = df.iloc[4:]
    df = df[df["Возраст"].notna()]
    df = df[df["Возраст"] != "nan"]

    # 4) Проходим по строкам: встречаем год → обновляем current_year;
    #    встречаем сноску → сохраняем отдельно;
    #    иначе считаем, что это возраст и добавляем запись в итоговый датасет.
    years = {str(y) for y in range(YEAR_MIN, YEAR_MAX + 1)}
    current_year = None
    rows: list[dict[str, Any]] = []
    footnotes: list[dict[str, Any]] = []

    for idx, row in df.iterrows():
        text = str(row["Возраст"]).strip()

        if is_year_row(text, years):
            current_year = text
            continue

        if is_footnote_row(text):
            footnotes.append({"id": len(footnotes) + 1, "text": text, "row_index": int(idx)})
            continue

        rows.append(
            {
                "year": current_year,
                "age": text,
                "total": row["Всего"],
                "men": row["Мужчины"],
                "women": row["Женщины"],
                "forecast_type": FORECAST_TYPE,
            }
        )

    # 5) Собираем DataFrame и немного "подчищаем" year.
    population_predict = pd.DataFrame(rows)
    if not population_predict.empty:
        # На случай, если сноска каким-то образом попала в year.
        population_predict = population_predict[~population_predict["year"].isin({f["text"] for f in footnotes})]
        population_predict["year"] = population_predict["year"].astype(str).str.replace(" год", "", regex=False)

    # 6) Сохраняем результаты: CSV, Parquet и отдельный JSON со сносками.
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / f"{out_stem}.csv"
    parquet_path = OUTPUT_DIR / f"{out_stem}.parquet"
    footnotes_json_path = OUTPUT_DIR / "footnotes_data.json"

    population_predict.to_csv(csv_path, index=False, encoding="utf-8-sig")
    population_predict.to_parquet(parquet_path, index=False, engine="pyarrow")

    footnotes_data = {
        "source_file": INPUT_XLS.name,
        "processing_date": pd.Timestamp.now().strftime("%Y-%m-%d"),
        "footnotes": footnotes,
    }
    with footnotes_json_path.open("w", encoding="utf-8") as f:
        json.dump(footnotes_data, f, ensure_ascii=False, indent=2)

    # 7) Короткий отчёт в консоль (как в ноутбуке).
    print(f"Найдено сносок: {len(footnotes)}")
    print(f"Записей в датасете: {len(population_predict)}")
    print("Датасет сохранен:")
    print(f"  - CSV: {csv_path.name}")
    print(f"  - Parquet: {parquet_path.name}")
    print(f"  - Footnotes JSON: {footnotes_json_path.name}")
    print(f"\nРазмер датасета: {population_predict.shape}")
    print(f"\nКолонки: {list(population_predict.columns)}")
    if not population_predict.empty and "year" in population_predict.columns:
        print(f"\nПериод: {population_predict['year'].min()} - {population_predict['year'].max()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

