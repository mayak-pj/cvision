"""Переименование файлов по результатам сопоставления и откат по логу.

Правила безопасности:
- используется только os.rename внутри той же папки: файлы не удаляются, не перемещаются
  и не перезаписываются;
- перед каждым переименованием проверяется, что целевое имя свободно (без учёта регистра,
  как в Windows). Существующий файл никогда не затирается;
- если новое имя занято другим файлом этой же пачки (обмен именами, цепочка, смена регистра),
  переименование идёт в три шага: исходное → временное, прямые переименования, временное → новое;
- каждый физический шаг сразу записывается в лог, поэтому по логу можно откатить даже прерванный запуск.
"""

import os
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import oplog
from .matcher import STATUS_MANUAL, STATUS_SIDE_RULE, FolderMatch
from .scanner import io_path

_INVALID_CHARS = set('<>:"/\\|?*') | {chr(c) for c in range(32)}
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)}
TEMP_MARK = ".~переименование_"


def name_key(name: str) -> str:
    """Ключ сравнения имён файлов как в Windows: без учёта регистра и формы Unicode."""
    return unicodedata.normalize("NFC", name).casefold()


def validate_filename(name: str) -> Optional[str]:
    """Сообщение об ошибке или None, если имя допустимо для файла Windows."""
    if not name or name in (".", ".."):
        return "пустое имя"
    bad = sorted(set(name) & _INVALID_CHARS)
    if bad:
        return "недопустимые символы: %s" % " ".join(repr(c) for c in bad)
    if name != name.rstrip(" ."):
        return "имя не может заканчиваться пробелом или точкой"
    if name.split(".")[0].strip().upper() in _RESERVED:
        return "зарезервированное имя Windows"
    if len(name) > 255:
        return "слишком длинное имя"
    return None


@dataclass
class RenameItem:
    old: str
    new: str
    category: str = ""
    confidence: Optional[float] = None
    note: str = ""          # пояснение распознавания (правило сторон, ручная правка)
    result: str = ""        # oplog.RESULT_*; пусто — ещё не выполнялось
    message: str = ""


@dataclass
class RenamePlan:
    folder: str
    items: List[RenameItem]
    kept: List[tuple] = field(default_factory=list)  # (имя, категория, уверенность, причина) — без изменений

    @property
    def pending(self) -> List[RenameItem]:
        return [i for i in self.items if not i.result]

    def count(self, result: str) -> int:
        return sum(i.result == result for i in self.items)


def _list_names(folder: str) -> List[str]:
    with os.scandir(io_path(folder)) as it:
        return [e.name for e in it]


def build_plan(match: FolderMatch) -> RenamePlan:
    """План переименований папки с проверкой конфликтов имён по текущему содержимому папки."""
    plan = RenamePlan(match.folder, [])
    for p in match.photos:
        if p.new_name:
            note = p.note if p.status in (STATUS_SIDE_RULE, STATUS_MANUAL) else ""
            plan.items.append(RenameItem(p.filename, p.new_name, p.category.name, p.confidence, note))
        else:
            category = p.category.name if p.identified else ""
            reason = p.note or p.status
            if not p.identified and p.candidates:
                reason += "; кандидаты: " + ", ".join("%s %.0f%%" % (c.category, c.confidence) for c in p.candidates)
            plan.kept.append((p.filename, category, p.confidence, reason))
    check_plan(plan)
    return plan


def check_plan(plan: RenamePlan, existing: Optional[List[str]] = None) -> None:
    """Помечает как «пропущено» пункты с недопустимым, повторяющимся или занятым новым именем."""
    if existing is None:
        existing = _list_names(plan.folder)
    sources = {name_key(i.old) for i in plan.pending}
    existing_keys = {name_key(n): n for n in existing}

    seen: Dict[str, RenameItem] = {}
    for item in plan.pending:
        error = validate_filename(item.new)
        key = name_key(item.new)
        if error:
            _skip(item, "недопустимое новое имя: " + error)
        elif key in seen:
            _skip(item, "новое имя совпадает с новым именем файла %s" % seen[key].old)
            _skip(seen[key], "новое имя совпадает с новым именем файла %s" % item.old)
        elif key in existing_keys and key not in sources:
            _skip(item, "имя «%s» уже занято файлом в папке" % existing_keys[key])
        elif name_key(item.old) not in existing_keys:
            _skip(item, "исходный файл не найден")
        seen.setdefault(key, item)


def _skip(item: RenameItem, message: str) -> None:
    item.result = oplog.RESULT_SKIPPED
    item.message = message


def _do_rename(folder: str, src: str, dst: str) -> None:
    """Одно переименование с защитой от затирания (на Linux/macOS os.rename молча заменил бы файл)."""
    src_path, dst_path = os.path.join(folder, src), os.path.join(folder, dst)
    if os.path.lexists(io_path(dst_path)):
        same = name_key(src) == name_key(dst) and os.path.samefile(io_path(src_path), io_path(dst_path))
        if not same:
            raise FileExistsError("имя «%s» уже занято" % dst)
    os.rename(io_path(src_path), io_path(dst_path))


def apply_plan(plan: RenamePlan, log: oplog.RunLog) -> RenamePlan:
    """Выполняет план. Ошибка одного файла не останавливает остальные. Результаты — в item.result."""
    folder = plan.folder
    try:
        check_plan(plan)  # папка могла измениться после предпросмотра
    except OSError as exc:
        for item in plan.pending:
            _skip(item, "папка недоступна: %s" % exc)

    for item in plan.items:
        if item.result == oplog.RESULT_SKIPPED:
            log.record(folder, oplog.ACTION_RENAME, oplog.RESULT_SKIPPED, item.old, item.new, item.old, item.new,
                       item.category, item.confidence, item.message)
    for name, category, confidence, reason in plan.kept:
        log.record(folder, oplog.ACTION_KEEP, oplog.RESULT_OK, name, name, name, "", category, confidence, reason)

    pending = plan.pending
    source_keys = {name_key(i.old) for i in pending}
    via_temp = [i for i in pending if name_key(i.new) in source_keys]
    direct = [i for i in pending if name_key(i.new) not in source_keys]
    run_id = uuid.uuid4().hex[:8]
    temp_names: Dict[int, str] = {}

    def step(item, action, src, dst):
        try:
            _do_rename(folder, src, dst)
        except OSError as exc:
            item.result, item.message = oplog.RESULT_ERROR, _describe(exc)
            log.record(folder, action, oplog.RESULT_ERROR, src, dst, item.old, item.new, item.category,
                       item.confidence, item.message)
            return False
        log.record(folder, action, oplog.RESULT_OK, src, dst, item.old, item.new, item.category, item.confidence,
                   item.note)
        return True

    for k, item in enumerate(via_temp):  # шаг 1: освобождаем имена, занятые файлами пачки
        temp = "%s%s%s_%d" % (item.old, TEMP_MARK, run_id, k)
        if step(item, oplog.ACTION_TEMP, item.old, temp):
            temp_names[id(item)] = temp
    for item in direct:  # шаг 2
        if step(item, oplog.ACTION_RENAME, item.old, item.new):
            item.result = oplog.RESULT_OK
    for item in via_temp:  # шаг 3
        temp = temp_names.get(id(item))
        if temp is None:
            continue
        if step(item, oplog.ACTION_RENAME, temp, item.new):
            item.result = oplog.RESULT_OK
            continue
        reason = item.message
        if step(item, oplog.ACTION_ROLLBACK, temp, item.old):  # вернуть исходное имя
            item.message = "%s; исходное имя восстановлено" % reason
        else:
            item.message = ("%s; файл остался под временным именем «%s» — выполните откат по логу"
                            % (reason, temp))
        item.result = oplog.RESULT_ERROR
    return plan


def rollback(log_path: str, log: oplog.RunLog, folder: Optional[str] = None) -> List[dict]:
    """Откат по CSV-логу: успешные физические шаги отменяются в обратном порядке.

    folder — откатить только эту папку (по умолчанию все папки из лога).
    Если файл после запуска переименовали или появился файл с исходным именем,
    шаг пропускается с ошибкой, а остальные выполняются.
    """
    rows = [r for r in oplog.read_log(log_path)
            if r["Действие"] in (oplog.ACTION_RENAME, oplog.ACTION_TEMP, oplog.ACTION_ROLLBACK)
            and r["Результат"] == oplog.RESULT_OK
            and (folder is None or os.path.normcase(r["Папка"]) == os.path.normcase(folder))]
    results = []
    for row in reversed(rows):
        folder_path, src, dst = row["Папка"], row["Стало"], row["Было"]
        entry = {"folder": folder_path, "from": src, "to": dst, "ok": False, "message": ""}
        try:
            if not os.path.lexists(io_path(os.path.join(folder_path, src))):
                raise FileNotFoundError("файл «%s» не найден (переименован или удалён после запуска)" % src)
            _do_rename(folder_path, src, dst)
            entry["ok"] = True
        except OSError as exc:
            entry["message"] = _describe(exc)
        log.record(folder_path, oplog.ACTION_ROLLBACK, oplog.RESULT_OK if entry["ok"] else oplog.RESULT_ERROR,
                   src, dst, row["Исходное имя"], row["Новое имя"], row["Категория"], None, entry["message"])
        results.append(entry)
    return results


def _describe(exc: OSError) -> str:
    if exc.filename is None and exc.args and isinstance(exc.args[0], str):
        return exc.args[0]  # сообщение, сформированное в этом модуле
    if isinstance(exc, PermissionError):
        return "нет доступа (файл открыт в другой программе или нет прав)"
    if isinstance(exc, FileExistsError):
        return "имя уже занято"
    if isinstance(exc, FileNotFoundError):
        return "файл не найден"
    return exc.strerror or str(exc)
