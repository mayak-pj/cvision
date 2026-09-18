"""Оценка точности по размеченной таблице (tools/make_labels.py) и подбор настроек. Инструмент разработчика.

    python tools/evaluate_labels.py --reference <эталон> logs/разметка/файл.xlsx [--grid]

Для каждой настройки считается по каждому фото:
    верно     — программа дала ту категорию, что в разметке;
    НЕВЕРНО   — программа дала другую категорию (самое опасное: неверное имя файла);
    не распознано — программа оставила фото без изменений (исправляется вручную в предпросмотре).
Фото с разметкой «Не относится ни к одной категории» считается верным, если программа его не тронула.
"""

import argparse
import itertools
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from openpyxl import load_workbook  # noqa: E402

from photo_renamer import features as feat  # noqa: E402
from photo_renamer import matcher  # noqa: E402
from photo_renamer.reference import category_key, load_reference  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_labels import HEADER_ROW, NOT_A_CATEGORY, TRUTH_COL  # noqa: E402


def read_labels(path):
    """[(папка, [имя файла], [ключ верной категории или None])]"""
    wb = load_workbook(path, read_only=True)
    out = []
    for ws in wb.worksheets:
        if ws.title in ("Инструкция", "Категории"):
            continue
        folder = ws["B1"].value
        names, truth = [], []
        for row in ws.iter_rows(min_row=HEADER_ROW + 1, values_only=True):
            if not row or not row[1]:
                continue
            names.append(row[1])
            label = (row[TRUTH_COL - 1] or "").strip()
            truth.append(None if not label or label == NOT_A_CATEGORY else category_key(label))
        out.append((folder, names, truth))
    return out


def score(result, truth):
    counts = Counter()
    wrong = []
    for p, t in zip(result.photos, truth):
        predicted = p.category.key if p.identified else None
        if predicted is None:
            counts["верно" if t is None else "не распознано"] += 1
        elif predicted == t:
            counts["верно"] += 1
        else:
            counts["НЕВЕРНО"] += 1
            wrong.append((p.filename, p.category.name, t or NOT_A_CATEGORY, p.confidence))
    return counts, wrong


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reference", "-r", required=True)
    parser.add_argument("labels")
    parser.add_argument("--grid", action="store_true", help="перебрать настройки")
    args = parser.parse_args()

    labels = read_labels(args.labels)
    reference = load_reference(args.reference)
    unknown = {t for _, _, truth in labels for t in truth if t is not None} - set(reference.categories)
    if unknown:
        sys.exit("В разметке есть категории, которых нет в эталоне: %s" % ", ".join(sorted(unknown)))
    total = sum(len(n) for _, n, _ in labels)
    print("Размечено фото: %d в %d папках" % (total, len(labels)))

    cache = {}

    def prepared(white_balance):
        if white_balance not in cache:
            ex = feat.FeatureExtractor(white_balance=white_balance)
            ref_feats, _ = feat.extract_many(ex, [p.path for p in reference.photos])
            model = matcher.ReferenceModel(reference, ex, ref_feats)
            folders = [(folder, names, feat.extract_many(ex, [os.path.join(folder, n) for n in names])[0], truth)
                       for folder, names, truth in labels]
            cache[white_balance] = (model, folders)
        return cache[white_balance]

    def evaluate(white_balance, config, show_errors=False):
        model, folders = prepared(white_balance)
        counts = Counter()
        per_folder = []
        for folder, names, fs, truth in folders:
            result = matcher.match_features(model, folder, names, fs, config)
            c, wrong = score(result, truth)
            counts += c
            per_folder.append((os.path.basename(folder), c))
            if show_errors and wrong:
                print("  Неверные в %s:" % folder)
                for name, got, expected, conf in wrong:
                    print("    %s: программа «%s» (%.0f%%), верно «%s»" % (name, got, conf, expected))
        return counts, per_folder

    def line(label, counts, per_folder):
        folders = "  ".join("%s: %d/%d/%d" % (f, c["верно"], c["НЕВЕРНО"], c["не распознано"]) for f, c in per_folder)
        return "%-58s верно %4d (%3.0f%%)  НЕВЕРНО %3d  не распознано %3d  | %s" % (
            label, counts["верно"], 100.0 * counts["верно"] / total, counts["НЕВЕРНО"], counts["не распознано"], folders)

    base = matcher.MatchConfig()
    for wb_flag in (False, True):
        counts, per_folder = evaluate(wb_flag, base, show_errors=not args.grid)
        print(line("по умолчанию, баланс по фону=%s" % wb_flag, counts, per_folder))
    if not args.grid:
        return

    weights = {
        "fg2+lbp1+hog1": {feat.FG_HIST: 2, feat.LBP: 1, feat.HOG: 1},
        "fg2+lbp2+hog1": {feat.FG_HIST: 2, feat.LBP: 2, feat.HOG: 1},
        "fg3+lbp2+hog1": {feat.FG_HIST: 3, feat.LBP: 2, feat.HOG: 1},
        "fg2+tiny1+lbp2+hog1": {feat.FG_HIST: 2, feat.TINY: 1, feat.LBP: 2, feat.HOG: 1},
    }
    rows = []
    for wb_flag, (wname, w), power, max_d, min_conf, temp in itertools.product(
            (False, True), weights.items(), (0, 2, 4, 6), (0.30, 0.35, 0.40), (40, 50, 60, 70), (0.03, 0.05)):
        config = matcher.MatchConfig(weights=w, adapt_power=power, max_distance=max_d, min_confidence=min_conf,
                                     temperature=temp, side_fill_max_distance=max_d + 0.10)
        counts, per_folder = evaluate(wb_flag, config)
        label = "wb=%d %s p=%d max_d=%.2f conf=%d T=%.2f" % (wb_flag, wname, power, max_d, min_conf, temp)
        rows.append((counts["НЕВЕРНО"], -counts["верно"], line(label, counts, per_folder)))
    print("\nЛучшие настройки (сначала меньше неверных, затем больше верных):")
    for _, _, text in sorted(rows)[:25]:
        print(text)


if __name__ == "__main__":
    main()
