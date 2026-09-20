"""Консольный режим (до появления GUI и для проверки на рабочей машине).

Проверка без переименования (по умолчанию):
    python run.py --reference <эталон> <папка проекта или SN> [...]
Переименование с подтверждением каждой папки:
    python run.py --reference <эталон> <папки> --apply
Откат по логу:
    python run.py --rollback logs/<файл>.csv
Анализ проекта: какие папки похожи и какие эталоны нужны:
    python run.py --groups <папка проекта>
"""

import argparse
import os
import sys
import time
from typing import List, Optional

from . import config as cfg
from . import grouping, oplog, renamer
from .cache import PhotoCache
from .matcher import ReferenceModel, match_folder
from .reference import describe, load_reference
from .report import build_report, format_report
from .scanner import find_photo_folders

YES = {"y", "yes", "д", "да"}


def _progress(prefix: str):
    def show(done, total):
        sys.stdout.write("\r%s: %d/%d" % (prefix, done, total))
        if done == total:
            sys.stdout.write("\n")
        sys.stdout.flush()
    return show


def _ask(question: str) -> bool:
    try:
        return input(question + " [д/н]: ").strip().lower() in YES
    except EOFError:
        return False


def run_batch(reference_dir: str, targets: List[str], config: cfg.AppConfig, apply: bool, assume_yes: bool,
              out=print) -> str:
    """Обработка папок по одной. Возвращает путь к CSV-логу."""
    reference = load_reference(reference_dir)
    out(describe(reference))
    if not reference.photos:
        raise SystemExit("В эталонной папке нет фото — обработка невозможна")

    scans, errors = [], []
    cache_dates = PhotoCache(config.cache_path(), config.cache_max_mb)
    for target in targets:
        result = find_photo_folders(target, exclude=[reference_dir], sort=config.sort,
                                    date_source=cache_dates.shot_time)
        scans.extend(result.folders)
        errors.extend(result.errors)
    cache_dates.close()
    for e in errors:
        out("ОШИБКА ДОСТУПА: %s — %s" % (e.path, e.message))
    out("Найдено папок с фото: %d" % len(scans))

    label = oplog.folder_label(targets[0]) if len(targets) == 1 else oplog.folder_label(targets[0]) + "_и_др"
    with oplog.RunLog(config.logs_path(), label, dry_run=not apply) as log:
        log.write_text("Запуск: %s\nРежим: %s\nЭталон: %s\nОбрабатываемые пути:\n%s\n\n%s"
                       % (oplog.now(), "переименование" if apply else "проверка (без переименования)",
                          reference_dir, "\n".join("  " + t for t in targets), describe(reference)))
        for e in errors:
            log.record(e.path, oplog.ACTION_KEEP, oplog.RESULT_ERROR, message=e.message)

        started = time.time()
        cache = PhotoCache(config.cache_path(), config.cache_max_mb)
        model = ReferenceModel.build(reference, config.extractor(), _progress("Эталон, чтение фото"),
                                     feature_source=cache.extract_many)
        for w in model.warnings:
            out("ВНИМАНИЕ: " + w)
            log.write_text("ВНИМАНИЕ: " + w)

        for n, scan in enumerate(scans, start=1):
            out("\nПапка %d из %d: %s" % (n, len(scans), scan.path))
            match = match_folder(model, scan.path, scan.photos, config.match, _progress("  чтение фото"),
                                 feature_source=cache.extract_many,
                                 shot_times=cache.shot_seconds(scan.photo_paths))
            try:
                plan = renamer.build_plan(match)
            except OSError as exc:
                out("ОШИБКА: папка недоступна (%s)" % exc)
                log.record(scan.path, oplog.ACTION_KEEP, oplog.RESULT_ERROR, message="папка недоступна: %s" % exc)
                continue
            report = build_report(match, len(scan.excluded), config.problem_categories)
            text = format_report(report, plan)
            out(text)

            if not apply:
                for item in plan.items:
                    log.record(scan.path, oplog.ACTION_PLAN, item.result or oplog.RESULT_OK, item.old, item.new,
                               item.old, item.new, item.category, item.confidence,
                               "; ".join(x for x in (item.note, item.message) if x))
                for name, category, confidence, reason in plan.kept:
                    log.record(scan.path, oplog.ACTION_KEEP, oplog.RESULT_OK, name, name, name, "", category,
                               confidence, reason)
                log.write_text(text)
                continue

            if not plan.pending:
                out("Переименовывать нечего.")
                log.write_text(text + "\nПереименовывать нечего.")
                continue
            if not assume_yes and not _ask("Переименовать %d файл(ов) в этой папке?" % len(plan.pending)):
                out("Папка пропущена пользователем.")
                log.write_text(text + "\nПапка пропущена пользователем.")
                continue
            renamer.apply_plan(plan, log)
            result_text = format_report(report, plan, applied=True)
            out(result_text.split("\n\n", 1)[-1])
            log.write_text(result_text)

        cache.close()
        out("\nГотово за %.0f с. Лог: %s\nОтчёт: %s" % (time.time() - started, log.csv_path, log.txt_path))
        return log.csv_path


def run_groups(roots: List[str], config: cfg.AppConfig, out=print) -> grouping.ProjectAnalysis:
    started = time.time()
    cache = PhotoCache(config.cache_path(), config.cache_max_mb)
    try:
        analysis = grouping.analyze_project(
            roots, config.extractor(), config.match.weights, config.group_threshold,
            feature_source=cache.extract_many, sort=config.sort, date_source=cache.shot_time,
            on_folder=lambda n, total, path: out("Папка %d из %d: %s" % (n, total, path)),
            on_progress=_progress("  чтение фото"))
    finally:
        cache.close()
    out(grouping.describe(analysis))
    out("Время: %.0f с" % (time.time() - started))
    return analysis


def run_rollback(log_path: str, config: cfg.AppConfig, folder: Optional[str] = None, out=print) -> str:
    label = "откат_" + os.path.splitext(os.path.basename(log_path))[0]
    with oplog.RunLog(config.logs_path(), label) as log:
        results = renamer.rollback(log_path, log, folder)
        ok = sum(r["ok"] for r in results)
        for r in results:
            if not r["ok"]:
                out("ОШИБКА: %s: %s → %s: %s" % (r["folder"], r["from"], r["to"], r["message"]))
        text = "Откат по логу %s: восстановлено %d, ошибок %d" % (log_path, ok, len(results) - ok)
        log.write_text(text)
        out(text + "\nЛог отката: " + log.csv_path)
        return log.csv_path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="run.py", description="Переименование фото комплектующих по эталону")
    parser.add_argument("targets", nargs="*", help="папка проекта или папки с серийными номерами")
    parser.add_argument("--reference", "-r", help="эталонная папка")
    parser.add_argument("--apply", action="store_true", help="переименовать (без флага — только проверка)")
    parser.add_argument("--yes", action="store_true", help="не спрашивать подтверждение для каждой папки")
    parser.add_argument("--rollback", metavar="CSV", help="откатить переименования по логу")
    parser.add_argument("--groups", action="store_true", help="анализ: группы похожих папок и нужные эталоны")
    parser.add_argument("--config", help="файл настроек (по умолчанию config.json рядом с программой)")
    args = parser.parse_args(argv)

    config, warnings = cfg.load_config(args.config)
    for w in warnings:
        print("НАСТРОЙКИ: " + w)

    if args.rollback:
        run_rollback(args.rollback, config)
    elif args.groups and args.targets:
        run_groups(args.targets, config)
    elif args.reference and args.targets:
        run_batch(args.reference, args.targets, config, args.apply, args.yes)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
