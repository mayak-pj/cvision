#!/usr/bin/env python3
"""Проверка репозитория перед публикацией: не утекают ли рабочие данные.

Проверяются только файлы, которые попадут в публикацию (git ls-files), и сообщения коммитов.
Ищутся:
  - файлы запрещённых типов (фото, сканы, логи, таблицы, база кэша), попавшие под версионный контроль;
  - сетевые пути Windows (\\\\сервер\\папка);
  - IP-адреса;
  - адреса электронной почты на непубличных доменах;
  - слова-маркеры из scripts/sensitive_terms.txt и scripts/sensitive_terms.local.txt
    (второй файл не версионируется — держите в нём реальные названия).

Найденные значения печатаются с замаскированной серединой, чтобы сам отчёт не стал утечкой.
Ненулевой код возврата означает «найдено» — скрипт можно повесить на pre-push.

Запуск:
    python scripts/check_before_push.py [--no-history] [--terms ДОПОЛНИТЕЛЬНЫЙ_ФАЙЛ]

Только стандартная библиотека Python.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".heic",   # фото и сканы
    ".csv", ".log", ".xlsx", ".xls",                             # логи и таблицы
    ".sqlite", ".db",                                            # кэш
}
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".ini", ".cfg", ".toml",
    ".bat", ".cmd", ".ps1", ".sh", ".html", ".css", ".js", "",
}
PUBLIC_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "icloud.com",
    "yandex.ru", "ya.ru", "mail.ru", "proton.me", "protonmail.com", "example.com", "example.org",
    "anthropic.com", "github.com", "users.noreply.github.com", "python.org",
}
MAX_FILE_BYTES = 2 * 1024 * 1024

UNC_RE = re.compile(r"\\\\[A-Za-z0-9_.\-]+\\[A-Za-z0-9_.\-]+")
IP_RE = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
MAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
VERSION_HINT_RE = re.compile(r"==|версия|version|\bv\d", re.IGNORECASE)


def mask(value: str) -> str:
    """Показывает начало и конец находки, середину скрывает."""
    value = value.strip()
    if len(value) <= 6:
        return value[0] + "…" if value else "…"
    return "%s…%s" % (value[:3], value[-3:])


def repo_root() -> Path:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
        return Path(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return Path(__file__).resolve().parent.parent


def tracked_files(root: Path):
    """Файлы под версионным контролем (именно они попадут на GitHub)."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True)
        return [root / name for name in out.stdout.decode("utf-8").split("\0") if name]
    except (OSError, subprocess.CalledProcessError):
        files = []
        for path in root.rglob("*"):
            if path.is_file() and ".git" not in path.parts:
                files.append(path)
        return files


def commit_messages(root: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), "log", "--format=%B%n%an %ae"],
                             capture_output=True, text=True, check=True)
        return out.stdout
    except (OSError, subprocess.CalledProcessError):
        return ""


def load_terms(paths):
    terms = []
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
    return terms


def scan_text(text: str, terms, skip_ip=False):
    """[(номер строки, тип находки, значение)] для одного текста."""
    findings = []
    lowered_terms = [(t, t.casefold()) for t in terms]
    for number, line in enumerate(text.splitlines(), start=1):
        for match in UNC_RE.finditer(line):
            findings.append((number, "сетевой путь Windows", match.group(0)))
        if not skip_ip and not VERSION_HINT_RE.search(line):
            for match in IP_RE.finditer(line):
                if all(int(part) <= 255 for part in match.groups()):
                    findings.append((number, "IP-адрес", match.group(0)))
        for match in MAIL_RE.finditer(line):
            if match.group(1).casefold() not in PUBLIC_MAIL_DOMAINS:
                findings.append((number, "почта на непубличном домене", match.group(0)))
        low = line.casefold()
        for original, folded in lowered_terms:
            if folded in low:
                findings.append((number, "слово из списка конфиденциальных", original))
    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-history", action="store_true", help="не проверять сообщения коммитов")
    parser.add_argument("--terms", action="append", default=[], help="дополнительный файл со словами-маркерами")
    args = parser.parse_args(argv)

    root = repo_root()
    scripts = root / "scripts"
    terms = load_terms([scripts / "sensitive_terms.txt", scripts / "sensitive_terms.local.txt"]
                       + [Path(p) for p in args.terms])
    problems = 0
    print("Проверка репозитория: %s" % root)
    print("Слов-маркеров в списке: %d" % len(terms))

    files = tracked_files(root)
    print("Файлов под версионным контролем: %d\n" % len(files))

    for path in files:
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            print("ЗАПРЕЩЁННЫЙ ТИП ФАЙЛА: %s" % path.relative_to(root))
            problems += 1

    for path in files:
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(root)
        skip_ip = relative.name.startswith("requirements")
        for number, kind, value in scan_text(text, terms, skip_ip=skip_ip):
            print("%s:%d: %s — %s" % (relative, number, kind, mask(value)))
            problems += 1

    if not args.no_history:
        for number, kind, value in scan_text(commit_messages(root), terms):
            print("сообщения коммитов: %s — %s" % (kind, mask(value)))
            problems += 1

    print()
    if problems:
        print("НАЙДЕНО: %d. Публиковать нельзя, пока не разберётесь с каждой находкой." % problems)
        return 1
    print("Чисто: запрещённых файлов и признаков рабочих данных не найдено.")
    print("Проверьте отдельно: имя и почта автора коммитов видны на GitHub "
          "(git log --format='%an %ae' | sort -u).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
