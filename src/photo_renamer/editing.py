"""Создание эталона вручную: из выделенных фото, категории и числа ракурсов — новые имена.

Правила (согласованы с заказчиком):
- выделенные фото берутся в порядке папки и делятся по `sides` подряд: это стороны одного экземпляра;
- нумерация экземпляров продолжает уже существующие в папке экземпляры этой категории;
- номер ставится, только если экземпляров больше одного. Поэтому при появлении второго
  экземпляра первый переименовывается: «Кулер» → «Кулер 1»;
- при одном ракурсе приписки «фото N» нет, при нескольких — «фото 1», «фото 2», …;
- расширение каждого файла сохраняется.

Модуль ничего не пишет на диск: он только строит план (renamer.RenamePlan).
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .reference import category_key, normalize_text, parse_name
from .renamer import RenameItem, RenamePlan, check_plan


@dataclass
class Assignment:
    """Что получится, если назначить выделенным фото категорию с заданным числом ракурсов."""

    plan: RenamePlan
    instances: List[int] = field(default_factory=list)   # номера новых экземпляров
    renumbered: int = 0                                  # сколько уже существующих фото переименовано
    incomplete: Optional[int] = None                     # у последнего экземпляра столько фото вместо sides
    warnings: List[str] = field(default_factory=list)


def existing_instances(names: Sequence[str], category: str) -> List[int]:
    """Номера экземпляров категории среди имён папки (без номера считается первым)."""
    key = category_key(category)
    found = set()
    for name in names:
        parsed = parse_name(name)
        if category_key(parsed.category) == key:
            found.add(parsed.instance or 1)
    return sorted(found)


def format_name(category: str, instance: Optional[int], side: Optional[int], ext: str) -> str:
    parts = [normalize_text(category)]
    if instance is not None:
        parts.append(str(instance))
    if side is not None:
        parts.append("фото %d" % side)
    return " ".join(parts) + ext


def assign(folder: str, names: Sequence[str], selected: Sequence[str], category: str, sides: int) -> Assignment:
    """План переименования выделенных фото. names — все фото папки в порядке обработки."""
    category = normalize_text(category)
    if not category:
        raise ValueError("Не выбрана категория")
    if sides < 1:
        raise ValueError("Число ракурсов должно быть не меньше 1")

    order = {name: i for i, name in enumerate(names)}
    chosen = sorted((s for s in selected if s in order), key=lambda s: order[s])
    if not chosen:
        raise ValueError("Не выделено ни одного фото")
    result = Assignment(RenamePlan(folder, []))
    key = category_key(category)

    # фото этой категории, уже названные в папке (выделенные не в счёт: их имена будут заменены)
    rest = [n for n in names if n not in set(chosen)]
    same_category = [n for n in rest if category_key(parse_name(n).category) == key]
    existing = existing_instances(same_category, category)
    next_number = (max(existing) + 1) if existing else 1
    groups = [chosen[i:i + sides] for i in range(0, len(chosen), sides)]
    result.instances = list(range(next_number, next_number + len(groups)))
    numbered = len(existing) + len(groups) > 1

    if numbered:
        # экземпляр был единственным и писался без номера — теперь он «Категория 1»
        for name in same_category:
            parsed = parse_name(name)
            if parsed.instance is not None:
                continue
            new = format_name(category, 1, parsed.side, os.path.splitext(name)[1])
            if new != name:
                result.plan.items.append(RenameItem(name, new, category, None, "нумерация экземпляров"))
                result.renumbered += 1

    for number, group in zip(result.instances, groups):
        for side, name in enumerate(group, start=1):
            new = format_name(category, number if numbered else None, side if sides > 1 else None,
                              os.path.splitext(name)[1])
            result.plan.items.append(RenameItem(name, new, category, 100.0))
        if len(group) != sides:
            result.incomplete = len(group)
            result.warnings.append("У последнего экземпляра %d фото вместо %d ракурсов" % (len(group), sides))

    check_plan(result.plan, existing=list(names))
    for item in result.plan.items:
        if item.result:
            result.warnings.append("%s → %s: %s" % (item.old, item.new, item.message))
    return result


def undo_plan(folder: str, done: Sequence[RenameItem]) -> RenamePlan:
    """Обратный план: вернуть исходные имена (для «Отменить» в редакторе)."""
    return RenamePlan(folder, [RenameItem(item.new, item.old, item.category, None, "отмена")
                               for item in reversed(list(done))])
