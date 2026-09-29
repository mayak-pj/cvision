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


def expected_names(reference, filenames, truth):
    """Правильные имена по разметке: подряд идущие фото одной категории — стороны и экземпляры,
    число сторон берётся из эталона, номера — по порядку появления."""
    names = [None] * len(filenames)
    index = 0
    counters = {}
    while index < len(filenames):
        key = truth[index]
        if key is None or key not in reference.categories:
            index += 1
            continue
        run = index
        while run < len(filenames) and truth[run] == key:
            run += 1
        category = reference.categories[key]
        group = list(range(index, run))
        for start in range(0, len(group), category.sides):
            chunk = group[start:start + category.sides]
            number = counters.get(key, 0) + 1
            counters[key] = number
            for side, position in enumerate(chunk, start=1):
                names[position] = category.format_name(number, side, os.path.splitext(filenames[position])[1])
        index = run
    return names


def _loose(name):
    """Имя без номера стороны: «Диск 2 фото 3» → «Диск 2».

    Номер стороны в разметке получен делением серии по счёту, а на реальных съёмках стороны
    снимают в разном порядке (в эталоне встречается и 1,2,3,4, и 3,4,1,2). Поэтому сравнивать
    номер стороны с такой «истиной» нельзя — сравнивается категория и номер экземпляра.
    """
    if name is None:
        return None
    return name.rsplit(" фото ", 1)[0] if " фото " in name else name


def score_names(result, expected, strict_sides=False):
    """Сравнение итоговых имён (категория + номер экземпляра + сторона).

    strict_sides=False: номер стороны не сравнивается — см. _loose.
    """
    counts = Counter()
    wrong = []
    for photo, want in zip(result.photos, expected):
        got = photo.new_name or photo.filename
        if not strict_sides:
            got, want = _loose(got), _loose(want)
        if want is None:
            counts["верно" if not photo.new_name else "НЕВЕРНО"] += 1
        elif got == want:
            counts["верно"] += 1
        elif not photo.new_name:
            counts["не переименовано"] += 1
        else:
            counts["НЕВЕРНО"] += 1
            wrong.append((photo.filename, got, want))
    return counts, wrong


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


def simulate_work(model, folder, names, features, truth, config):
    """Как работает человек: идёт по списку сверху вниз и исправляет неверные имена.

    Возвращает (правок, счёт после работы). Каждая правка — это подтверждение категории,
    то есть ещё и пример для библиотеки: следующие фото программа уже узнаёт сама.
    Считаем самый невыгодный порядок — подряд, а не «сначала сомнительные».
    """
    result = matcher.match_features(model, folder, names, features, config)
    fixes = 0
    for index in range(len(names)):
        want = truth[index]
        photo = result.photos[index]
        got = photo.category.key if photo.identified else None
        if got == want:
            continue
        fixes += 1
        matcher.set_category(result, index, want)      # None — «не переименовывать»
    check = sum(p.status == matcher.STATUS_CHECK for p in result.photos)
    return fixes, score(result, truth), check


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reference", "-r", required=True)
    parser.add_argument("labels")
    parser.add_argument("--grid", action="store_true", help="перебрать настройки")
    parser.add_argument("--learning", action="store_true",
                        help="сравнить работу с библиотекой примеров и без неё")
    parser.add_argument("--no-clip", action="store_true", help="считать без модели CLIP")
    args = parser.parse_args()

    labels = read_labels(args.labels)
    reference = load_reference(args.reference)
    encoder = None
    if not args.no_clip:
        from photo_renamer import config as cfg
        encoder = cfg.AppConfig().encoder()
    print("Модель CLIP: %s" % ("подключена" if encoder is not None else "нет"))
    unknown = {t for _, _, truth in labels for t in truth if t is not None} - set(reference.categories)
    if unknown:
        sys.exit("В разметке есть категории, которых нет в эталоне: %s" % ", ".join(sorted(unknown)))
    total = sum(len(n) for _, n, _ in labels)
    print("Размечено фото: %d в %d папках" % (total, len(labels)))

    order_cache = {}

    def in_shooting_order(folder, names, truth):
        """Порядок съёмки (как в программе): по дате из EXIF, фото без даты — в порядке имён."""
        if folder not in order_cache:
            from photo_renamer.cache import PhotoCache
            photos = PhotoCache(os.path.join("cache", "photos.sqlite"))
            dates = {n: photos.shot_time(os.path.join(folder, n)) for n in names}
            photos.close()
            order = sorted(range(len(names)), key=lambda i: (dates[names[i]] is None, dates[names[i]] or "", i))
            order_cache[folder] = order
        order = order_cache[folder]
        return [names[i] for i in order], [truth[i] for i in order]

    labels = [(folder,) + in_shooting_order(folder, names, truth) for folder, names, truth in labels]

    cache = {}

    def prepared(white_balance):
        if white_balance not in cache:
            # модель CLIP подключается так же, как в программе: без неё замер получается
            # не про то, что работает на рабочей машине
            ex = feat.FeatureExtractor(white_balance=white_balance, encoder=encoder)
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

    # точность по именам целиком: категория + номер экземпляра + сторона
    model, prepared_folders = prepared(False)
    total_names = Counter()
    for folder, names, fs, truth in prepared_folders:
        result = matcher.match_features(model, folder, names, fs, base)
        counts, wrong = score_names(result, expected_names(reference, names, truth))
        total_names += counts
        print("  имена целиком, %s: верно %d, НЕВЕРНО %d, не переименовано %d"
              % (os.path.basename(folder), counts["верно"], counts["НЕВЕРНО"], counts["не переименовано"]))
        for filename, got, want in wrong[:8]:
            print("      %s: «%s» вместо «%s»" % (filename, got, want))
    print("  имена целиком, всего: верно %d из %d (%.0f%%), НЕВЕРНО %d, не переименовано %d"
          % (total_names["верно"], total, 100.0 * total_names["верно"] / total, total_names["НЕВЕРНО"],
             total_names["не переименовано"]))
    if args.learning:
        print()
        print("Библиотека примеров: сколько правок нужно, чтобы папка стала верной целиком")
        for learn in (False, True):
            config = matcher.MatchConfig(learn_from_confirmed=learn)
            model, prepared_folders = prepared(False)
            for folder, names, fs, truth in prepared_folders:
                fixes, (counts, _), check = simulate_work(model, folder, names, fs, truth, config)
                print("  %-28s обучение %-3s: правок %3d из %3d фото, после работы верно %3d,"
                      " помечено «ПРОВЕРЬТЕ» %3d"
                      % (os.path.basename(folder), "да" if learn else "нет", fixes, len(names),
                         counts["верно"], check))

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
