"""Локальный кэш миниатюр и признаков фото (SQLite, рядом с программой).

Фото на сетевом диске весит ~15 МБ. Одно чтение даёт и миниатюру для редактора эталона,
и признаки для распознавания. Кэш избавляет от повторного чтения по сети.

Ключ записи — размер файла + SHA-1 первых и последних 64 КБ. Поэтому кэш
- переживает переименование файла и перенос папки (содержимое то же);
- не путает разные фото, даже если у них совпадают размер и время изменения.
Признаки хранятся вместе с версией способа расчёта (FeatureExtractor.version()): при смене
настроек признаки пересчитываются, а миниатюры остаются.
Размер кэша ограничен, при превышении удаляются записи, которые дольше всего не использовались.

Фото только читаются. В обрабатываемые папки кэш ничего не пишет.
"""

import hashlib
import io
import os
import pickle
import sqlite3
import time
from typing import List, Optional, Sequence, Tuple

from PIL import Image

from . import features as feat
from .scanner import io_path

THUMB_SIZE = feat.THUMB_SIZE
THUMB_QUALITY = feat.THUMB_QUALITY
_EDGE = 64 * 1024
_EVICT_EVERY = 200  # проверять размер кэша раз в столько записей


def file_key(path: str) -> str:
    """Ключ содержимого файла: размер + SHA-1 первых и последних 64 КБ."""
    p = io_path(path)
    size = os.path.getsize(p)
    h = hashlib.sha1(str(size).encode())
    with open(p, "rb") as f:
        h.update(f.read(_EDGE))
        if size > 2 * _EDGE:
            f.seek(-_EDGE, os.SEEK_END)
            h.update(f.read(_EDGE))
    return h.hexdigest()


class PhotoCache:
    def __init__(self, path: str, max_mb: float = 2048):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.max_bytes = int(max_mb * 1024 * 1024)
        self._db = sqlite3.connect(path)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS thumbs (key TEXT PRIMARY KEY, jpeg BLOB, used REAL);
            CREATE TABLE IF NOT EXISTS feats (key TEXT, version TEXT, data BLOB, used REAL,
                                              PRIMARY KEY (key, version));
        """)
        self._writes = 0

    def close(self) -> None:
        self._db.commit()
        self._db.close()

    # --- чтение одного фото ---------------------------------------------------------

    def _compute(self, path: str, key: str, extractor: Optional[feat.FeatureExtractor]):
        img = feat.load_image(path, max(extractor.work_size if extractor else 0, feat.DECODE_SIZE))
        thumb = feat.thumbnail_jpeg(img)
        features = extractor.from_thumbnail(thumb) if extractor else None
        now = time.time()
        self._db.execute("INSERT OR REPLACE INTO thumbs VALUES (?, ?, ?)", (key, thumb, now))
        if features is not None:
            self._db.execute("INSERT OR REPLACE INTO feats VALUES (?, ?, ?, ?)",
                             (key, extractor.version(), pickle.dumps(features, protocol=4), now))
        self._after_write()
        return thumb, features

    def get(self, path: str, extractor: Optional[feat.FeatureExtractor] = None) -> Tuple[bytes, Optional[feat.Features]]:
        """(миниатюра JPEG, признаки). Фото читается целиком, только если чего-то нет в кэше.
        Ошибки чтения — feat.FeatureError."""
        try:
            key = file_key(path)
        except FileNotFoundError:
            raise feat.FeatureError(path, "файл не найден")
        except PermissionError:
            raise feat.FeatureError(path, "нет доступа к файлу")
        except OSError as exc:
            raise feat.FeatureError(path, "не удалось прочитать файл (%s)" % exc)

        now = time.time()
        row = self._db.execute("SELECT jpeg FROM thumbs WHERE key = ?", (key,)).fetchone()
        features = None
        if extractor is not None:
            frow = self._db.execute("SELECT data FROM feats WHERE key = ? AND version = ?",
                                    (key, extractor.version())).fetchone()
            if frow is not None:
                features = pickle.loads(frow[0])
                self._db.execute("UPDATE feats SET used = ? WHERE key = ? AND version = ?",
                                 (now, key, extractor.version()))
        if row is not None and extractor is not None and features is None:
            # миниатюра есть, а признаков нет (сменились настройки) — считаем из миниатюры,
            # не перечитывая фото по сети
            features = extractor.from_thumbnail(row[0])
            self._db.execute("INSERT OR REPLACE INTO feats VALUES (?, ?, ?, ?)",
                             (key, extractor.version(), pickle.dumps(features, protocol=4), now))
            self._after_write()
        if row is None:
            return self._compute(path, key, extractor)
        self._db.execute("UPDATE thumbs SET used = ? WHERE key = ?", (now, key))
        return row[0], features

    def thumbnail(self, path: str) -> Image.Image:
        jpeg, _ = self.get(path)
        return Image.open(io.BytesIO(jpeg))

    def features(self, path: str, extractor: feat.FeatureExtractor) -> feat.Features:
        return self.get(path, extractor)[1]

    def extract_many(self, extractor: feat.FeatureExtractor, paths: Sequence[str], on_progress=None):
        """Как features.extract_many, но через кэш. Возвращает (features, errors)."""
        out: List = []
        errors: List[feat.FeatureError] = []
        for i, path in enumerate(paths):
            try:
                out.append(self.features(path, extractor))
            except feat.FeatureError as exc:
                out.append(None)
                errors.append(exc)
            if on_progress:
                on_progress(i + 1, len(paths))
        self._db.commit()
        return out, errors

    # --- размер -------------------------------------------------------------------------

    def _after_write(self) -> None:
        self._writes += 1
        if self._writes % _EVICT_EVERY == 0:
            self._db.commit()
            self.evict()

    def size_bytes(self) -> int:
        a = self._db.execute("SELECT COALESCE(SUM(LENGTH(jpeg)), 0) FROM thumbs").fetchone()[0]
        b = self._db.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) FROM feats").fetchone()[0]
        return a + b

    def evict(self) -> None:
        """Удаляет давно не использованные записи, пока кэш больше лимита (с запасом 10%)."""
        total = self.size_bytes()
        if total <= self.max_bytes:
            return
        target = int(self.max_bytes * 0.9)
        rows = self._db.execute("SELECT key, used, LENGTH(jpeg) FROM thumbs ORDER BY used").fetchall()
        for key, _, size in rows:
            if total <= target:
                break
            feat_size = self._db.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) FROM feats WHERE key = ?",
                                         (key,)).fetchone()[0]
            self._db.execute("DELETE FROM thumbs WHERE key = ?", (key,))
            self._db.execute("DELETE FROM feats WHERE key = ?", (key,))
            total -= size + feat_size
        self._db.commit()
        self._db.execute("VACUUM")
