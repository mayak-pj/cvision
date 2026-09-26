"""Список категорий для редактора эталона: names.txt рядом с программой.

Формат файла — одно имя в строке, при желании через «|» обычное число ракурсов:

    Плата оперативной памяти | 2
    Вентилятор | 1
    Кулер

Число ракурсов программа запоминает сама после каждого переименования, чтобы
не выбирать его заново: у памяти обычно 2 ракурса, у вентилятора 1, у процессора
в одном проекте 2, в другом 3.

Поиск: каждое введённое слово должно совпасть с началом какого-нибудь слова имени
(«оп пам» → «Плата оперативной памяти»). «Ё» и «е» не различаются. Сначала идут имена,
которые начинаются с запроса.
"""

import os
from typing import Dict, Iterable, List, Optional, Tuple

from .reference import fix_mixed_script, looks_like_category

DEFAULT_SIDES = 1
SEPARATOR = "|"


def fold(text: str) -> str:
    """Ключ для поиска и сортировки: регистр, «ё»/«е» и латинские двойники не различаются."""
    return fix_mixed_script(text).casefold().replace("ё", "е")


def _words(text: str) -> List[str]:
    return [w for w in fold(text).replace("-", " ").replace(",", " ").split() if w]


def filter_names(query: str, names: Iterable[str]) -> List[str]:
    """Имена, подходящие под запрос: каждое слово запроса — начало какого-нибудь слова имени.
    Сначала те, что начинаются с запроса. Если совпадений нет — поиск по вхождению."""
    names = list(names)
    query = fold(query).strip()
    if not query:
        # без запроса список показывается по алфавиту: так добавленная категория встаёт
        # на своё место, а не в конец. Порядок в файле при этом не меняется
        return sorted(names, key=fold)
    tokens = _words(query)
    matches = []
    for name in names:
        words = _words(name)
        if all(any(word.startswith(token) for word in words) for token in tokens):
            rank = 0 if fold(name).startswith(query) else (1 if words and words[0].startswith(tokens[0]) else 2)
            matches.append((rank, fold(name), name))
    if not matches:
        matches = [(3, fold(name), name) for name in names if query in fold(name)]
    return [name for _, _, name in sorted(matches)]


class NamesList:
    """Имена категорий и обычное число ракурсов для каждого."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._sides: Dict[str, int] = {}   # ключ (fold) → ракурсы
        self._names: Dict[str, str] = {}   # ключ (fold) → как пишется
        self._order: List[str] = []        # ключи в порядке файла
        if path and os.path.exists(path):
            self.load()

    # --- файл ---------------------------------------------------------------------

    def load(self) -> List[str]:
        """Читает файл. Возвращает предупреждения (пустые/повторные строки)."""
        warnings = []
        self._sides.clear()
        self._names.clear()
        self._order.clear()
        with open(self.path, encoding="utf-8-sig") as f:
            for number, line in enumerate(f, start=1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                name, _, sides_text = line.partition(SEPARATOR)
                name = " ".join(name.split())
                if not name:
                    continue
                sides = DEFAULT_SIDES
                if sides_text.strip():
                    try:
                        sides = max(1, int(sides_text.strip()))
                    except ValueError:
                        warnings.append("Строка %d: «%s» — число ракурсов не распознано" % (number, line))
                if fold(name) in self._names:
                    warnings.append("Строка %d: «%s» уже есть в списке" % (number, name))
                    continue
                if not looks_like_category(name):
                    # Мусор, попавший в файл прежней версией программы из имён вроде «file (1).jpg»
                    # или «File 1.jpg». Заодно отсеиваются имена со служебными знаками: такое имя
                    # программа всё равно не смогла бы записать в файл по шаблону. При следующем
                    # сохранении списка строка уходит из файла
                    warnings.append("Строка %d: «%s» — не название комплектующего" % (number, name))
                    continue
                self.add(name, sides)
        return warnings

    def save(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            for key in self._order:
                f.write("%s %s %d\n" % (self._names[key], SEPARATOR, self._sides[key]))

    # --- содержимое ---------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._order)

    def __contains__(self, name: str) -> bool:
        return fold(name) in self._names

    def names(self) -> List[str]:
        return [self._names[k] for k in self._order]

    def sides_for(self, name: str, default: int = DEFAULT_SIDES) -> int:
        return self._sides.get(fold(name), default)

    def add(self, name: str, sides: int = DEFAULT_SIDES) -> str:
        """Добавляет имя (или обновляет число ракурсов). Возвращает написание имени в списке."""
        # латинская буква, попавшая в русское слово не той раскладкой, чинится сразу: иначе она
        # разойдётся по именам файлов целого проекта
        name = fix_mixed_script(name)
        key = fold(name)
        if not key:
            raise ValueError("Пустое имя")
        if key not in self._names:
            self._order.append(key)
            self._names[key] = name
        self._sides[key] = max(1, int(sides))
        return self._names[key]

    def remove(self, name: str) -> bool:
        key = fold(name)
        if key not in self._names:
            return False
        self._order.remove(key)
        del self._names[key]
        del self._sides[key]
        return True

    def remember_sides(self, name: str, sides: int) -> None:
        """Запоминает число ракурсов после переименования."""
        self.add(name, sides)

    def learn_from(self, categories: Iterable[Tuple[str, int]]) -> None:
        """Добавляет категории из эталона проекта (имя, число сторон).

        Имена, непохожие на название комплектующего, пропускаются: список категорий заполняется
        здесь без участия человека, и мусор из неназванных кадров в него попадать не должен.
        Введённое руками имя (add) не проверяется — там человек знает, что пишет.
        """
        for name, sides in categories:
            if looks_like_category(name):
                self.add(name, sides)

    # --- поиск --------------------------------------------------------------------

    def search(self, query: str) -> List[str]:
        return filter_names(query, self.names())
