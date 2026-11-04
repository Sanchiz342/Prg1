"""Builds dbt/seeds/regions.csv: dataset region name <-> Ukrainian name <-> ISO 3166-2 code <-> map shape.

Nothing is typed in by hand except the English-name -> map-shape pairing (the two sources transliterate
differently: "Vinnytska oblast" vs "Vinnytsia Oblast"). Everything else is derived from the data and checked:
  * Ukrainian names: the dataset's _en and _uk files are row-aligned translations; we verify every row has the
    same timestamps and that each English name maps to exactly one Ukrainian name.
  * ISO codes come from the geoBoundaries file itself.
  * Every dataset region must map to exactly one shape; shapes without alerts data are listed explicitly.

Usage: python scripts/build_region_seed.py <official_en.csv> <official_uk.csv> <volunteer_en.csv> <regions.geojson>
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

SHAPE_FOR = {
    "Cherkaska oblast": "Cherkasy Oblast",
    "Chernihivska oblast": "Chernihiv Oblast",
    "Chernivetska oblast": "Chernivtsi Oblast",
    "Dnipropetrovska oblast": "Dnipropetrovsk Oblast",
    "Donetska oblast": "Donetsk Oblast",
    "Ivano-Frankivska oblast": "Ivano-Frankivsk Oblast",
    "Kharkivska oblast": "Kharkiv Oblast",
    "Khersonska oblast": "Kherson Oblast",
    "Khmelnytska oblast": "Khmelnytskyi Oblast",
    "Kirovohradska oblast": "Kirovohrad Oblast",
    "Kyiv City": "Kyiv",
    "Kyivska oblast": "Kyiv Oblast",
    "Luhanska oblast": "Luhansk Oblast",
    "Lvivska oblast": "Lviv Oblast",
    "Mykolaivska oblast": "Mykolaiv Oblast",
    "Odeska oblast": "Odessa Oblast",
    "Poltavska oblast": "Poltava Oblast",
    "Rivnenska oblast": "Rivne Oblast",
    "Sumska oblast": "Sumy Oblast",
    "Ternopilska oblast": "Ternopil Oblast",
    "Vinnytska oblast": "Vinnytsia Oblast",
    "Volynska oblast": "Volyn Oblast",
    "Zakarpatska oblast": "Zakarpattia Oblast",
    "Zaporizka oblast": "Zaporizhia Oblast",
    "Zhytomyrska oblast": "Zhytomyr Oblast",
}
# Map shapes for which the dataset has no alert records (its README: Crimea is under a permanent alert that is
# not listed in the data).
NOT_IN_DATASET = {
    "Autonomous Republic of Crimea": ("Автономна Республіка Крим", "permanent alert, not listed in the dataset"),
    "Sevastopol": ("м. Севастополь", "not covered by the dataset"),
}


def main(official_en: Path, official_uk: Path, volunteer_en: Path, geojson: Path, out: Path) -> None:
    uk_for: dict[str, set[str]] = defaultdict(set)
    with official_en.open(encoding="utf-8", newline="") as fe, official_uk.open(encoding="utf-8", newline="") as fu:
        for i, (en, uk) in enumerate(zip(csv.DictReader(fe), csv.DictReader(fu), strict=True)):
            if (en["started_at"], en["finished_at"]) != (uk["started_at"], uk["finished_at"]):
                raise SystemExit(f"row {i + 2}: _en and _uk files are not row-aligned")
            uk_for[en["oblast"]].add(uk["oblast"])
    ambiguous = {k: v for k, v in uk_for.items() if len(v) != 1}
    if ambiguous:
        raise SystemExit(f"ambiguous translations: {ambiguous}")

    with volunteer_en.open(encoding="utf-8", newline="") as f:
        volunteer_regions = {r["region"] for r in csv.DictReader(f)}
    if volunteer_regions != set(uk_for):
        raise SystemExit(f"region sets differ: {volunteer_regions ^ set(uk_for)}")

    shapes = {f["properties"]["shapeName"]: f["properties"]["shapeISO"] for f in json.loads(geojson.read_text())["features"]}
    if set(SHAPE_FOR) != set(uk_for):
        raise SystemExit(f"SHAPE_FOR does not cover the dataset regions: {set(SHAPE_FOR) ^ set(uk_for)}")
    if missing := {s for s in SHAPE_FOR.values() if s not in shapes}:
        raise SystemExit(f"shapes not in geojson: {missing}")
    if leftover := set(shapes) - set(SHAPE_FOR.values()) - set(NOT_IN_DATASET):
        raise SystemExit(f"unmapped shapes: {leftover}")

    rows = [
        {"region": en, "name_uk": next(iter(uk_for[en])), "iso_code": shapes[SHAPE_FOR[en]],
         "shape_name": SHAPE_FOR[en], "in_dataset": "true", "note": ""}
        for en in sorted(uk_for)
    ]
    rows += [
        {"region": "", "name_uk": uk, "iso_code": shapes[shape], "shape_name": shape, "in_dataset": "false", "note": note}
        for shape, (uk, note) in sorted(NOT_IN_DATASET.items())
    ]
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} regions to {out}")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    main(*map(Path, sys.argv[1:]), Path(__file__).resolve().parent.parent / "dbt" / "seeds" / "regions.csv")
