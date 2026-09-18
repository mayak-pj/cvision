"""Отчёт по обработанной папке: % распознавания, расхождения с эталоном, неопознанные фото,
сообщения по «проблемным» категориям, план или результат переименования."""

from dataclasses import dataclass, field
from typing import List, Optional

from . import oplog
from .matcher import STATUS_ALREADY_NAMED, FolderMatch, PhotoMatch
from .reference import category_key
from .renamer import RenamePlan


@dataclass
class CategoryDiff:
    name: str
    expected: int
    actual: int


@dataclass
class FolderReport:
    folder: str
    total: int
    identified: int
    already_named: int
    excluded: int                        # рентгенограммы (в статистике не участвуют)
    missing: List[CategoryDiff]
    extra: List[CategoryDiff]
    unidentified: List[PhotoMatch]
    problem_categories: List[str]        # проблемные категории, которые не удалось распознать
    warnings: List[str] = field(default_factory=list)

    @property
    def percent(self) -> float:
        return 100.0 * self.identified / self.total if self.total else 100.0

    @property
    def matches_reference(self) -> bool:
        return not self.missing and not self.extra and not self.unidentified


def build_report(match: FolderMatch, excluded: int = 0, problem_categories: Optional[List[str]] = None) -> FolderReport:
    reference = match.model.reference
    expected = reference.expected_counts()
    actual = match.category_counts()

    missing, extra = [], []
    for name, count in expected.items():
        got = actual.get(name, 0)
        if got < count:
            missing.append(CategoryDiff(name, count, got))
        elif got > count:
            extra.append(CategoryDiff(name, count, got))

    unidentified = match.unidentified()
    problem_keys = {category_key(c) for c in (problem_categories or [])}
    involved = []
    for cat in reference.categories.values():
        if cat.key not in problem_keys:
            continue
        short = any(d.name == cat.name for d in missing)
        suspected = any(c.category == cat.name for p in unidentified for c in p.candidates[:2])
        if short or suspected:
            involved.append(cat.name)

    return FolderReport(
        folder=match.folder,
        total=len(match.photos),
        identified=sum(p.identified for p in match.photos),
        already_named=sum(p.status == STATUS_ALREADY_NAMED for p in match.photos),
        excluded=excluded,
        missing=missing,
        extra=extra,
        unidentified=unidentified,
        problem_categories=involved,
        warnings=list(match.warnings),
    )


def _candidates(photo: PhotoMatch) -> str:
    if not photo.candidates:
        return ""
    return "кандидаты: " + ", ".join("%s %.0f%%" % (c.category, c.confidence) for c in photo.candidates)


def format_report(report: FolderReport, plan: Optional[RenamePlan] = None, applied: bool = False) -> str:
    lines = ["=" * 78, "Папка: %s" % report.folder, "Дата: %s" % oplog.now()]
    photos = "Фото .jpg: %d" % report.total
    if report.excluded:
        photos += " (рентгенограмм пропущено: %d)" % report.excluded
    lines.append(photos)
    lines.append("Распознано: %d из %d (%.0f%%)" % (report.identified, report.total, report.percent))
    if report.already_named:
        lines.append("  из них уже были названы по эталону ранее: %d" % report.already_named)

    if report.matches_reference:
        lines.append("Состав совпадает с эталоном.")
    if report.missing or report.extra:
        lines.append("Расхождения с эталоном (фото распознано / в эталоне):")
        for d in report.missing:
            lines.append("  не хватает: %s — %d из %d" % (d.name, d.actual, d.expected))
        for d in report.extra:
            lines.append("  лишние:     %s — %d при %d в эталоне" % (d.name, d.actual, d.expected))

    if report.unidentified:
        lines.append("Неопознанные фото (%d), имена не изменены:" % len(report.unidentified))
        for p in report.unidentified:
            details = "; ".join(x for x in (p.note, _candidates(p)) if x)
            lines.append("  %s — %s" % (p.filename, details))
    if report.problem_categories:
        lines.append("НЕ УДАЛОСЬ РАСПОЗНАТЬ АВТОМАТИЧЕСКИ: %s — проверьте вручную"
                     % ", ".join(report.problem_categories))
    for w in report.warnings:
        lines.append("Внимание: " + w)

    if plan is not None:
        lines.append("")
        if not applied:
            lines.append("План переименования (%d):" % len(plan.items))
            for item in plan.items:
                mark = "  [ПРОПУСК: %s]" % item.message if item.result == oplog.RESULT_SKIPPED else ""
                if item.note:
                    mark = "  — " + item.note + mark
                lines.append("  %s → %s  (%.0f%%)%s" % (item.old, item.new, item.confidence or 0, mark))
        else:
            lines.append("Результат: переименовано %d, пропущено %d, ошибок %d"
                         % (plan.count(oplog.RESULT_OK), plan.count(oplog.RESULT_SKIPPED),
                            plan.count(oplog.RESULT_ERROR)))
            for item in plan.items:
                if item.result != oplog.RESULT_OK:
                    lines.append("  %s → %s: %s (%s)" % (item.old, item.new, item.result, item.message))
    return "\n".join(lines)
