"""Лог операций (CSV) и текстовый отчёт запуска. Всё пишется РЯДОМ С ПРОГРАММОЙ (logs/),
а не в обрабатываемые папки.

Один запуск = один CSV-файл и один TXT-файл. В имени — дата/время и имя обработанной папки.
В каждой строке CSV есть полный путь к папке, поэтому лог однозначен даже спустя много запусков.
CSV пишется в UTF-8 с BOM и разделителем «;», чтобы русский Excel открывал его без настройки.
Каждая строка сразу сбрасывается на диск: при сбое лог остаётся полным, и по нему можно сделать откат.
"""

import csv
import os
import re
import time
from typing import Dict, List, Optional

COLUMNS = [
    "Дата и время",
    "Папка",
    "Действие",
    "Результат",
    "Было",
    "Стало",
    "Исходное имя",
    "Новое имя",
    "Категория",
    "Уверенность, %",
    "Сообщение",
]

ACTION_PLAN = "план"
ACTION_RENAME = "переименование"
ACTION_TEMP = "временное имя"
ACTION_KEEP = "без изменений"
ACTION_ROLLBACK = "откат"

RESULT_OK = "ок"
RESULT_ERROR = "ошибка"
RESULT_SKIPPED = "пропущено"

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def sanitize(name: str, max_len: int = 80) -> str:
    """Имя, пригодное для файла Windows: недопустимые символы → «_», без точек и пробелов по краям."""
    name = _INVALID.sub("_", name).strip(" .")
    name = re.sub(r"_+", "_", name)
    return name[:max_len].rstrip(" .") or "папка"


def folder_label(path: str) -> str:
    """Метка папки для имени лога: две последние части пути (проект и серийный номер)."""
    parts = [p for p in re.split(r"[\\/]+", os.path.normpath(path)) if p and not p.endswith(":")]
    return sanitize("_".join(parts[-2:]) if parts else path)


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class RunLog:
    def __init__(self, logs_dir: str, label: str, dry_run: bool = False):
        os.makedirs(logs_dir, exist_ok=True)
        stem = "%s_%s%s" % (time.strftime("%Y-%m-%d_%H-%M-%S"), sanitize(label), "_проверка" if dry_run else "")
        base, k = os.path.join(logs_dir, stem), 1
        while os.path.exists(base + ".csv") or os.path.exists(base + ".txt"):
            k += 1
            base = os.path.join(logs_dir, "%s_%d" % (stem, k))
        self.csv_path = base + ".csv"
        self.txt_path = base + ".txt"
        self.dry_run = dry_run
        self._csv = open(self.csv_path, "w", encoding="utf-8-sig", newline="")
        self._writer = csv.writer(self._csv, delimiter=";")
        self._writer.writerow(COLUMNS)
        self._flush()

    def record(self, folder: str, action: str, result: str, from_name: str = "", to_name: str = "",
               original: str = "", new: str = "", category: str = "", confidence: Optional[float] = None,
               message: str = "") -> None:
        conf = "" if confidence is None else ("%.1f" % confidence).replace(".", ",")
        self._writer.writerow([now(), folder, action, result, from_name, to_name, original, new, category, conf,
                               message])
        self._flush()

    def write_text(self, text: str) -> None:
        with open(self.txt_path, "a", encoding="utf-8-sig") as f:
            f.write(text.rstrip("\n") + "\n\n")

    def _flush(self) -> None:
        self._csv.flush()
        try:
            os.fsync(self._csv.fileno())
        except OSError:
            pass

    def close(self) -> None:
        if not self._csv.closed:
            self._csv.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_log(path: str) -> List[Dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))
