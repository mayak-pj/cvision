"""Просмотр фото крупно. Одно окно на редактор эталона и на пакетное переименование."""

import os
from typing import List, Optional

import tkinter as tk
from tkinter import ttk

from .. import features as feat
from ..cache import PhotoCache
from .thumbgrid import to_photo

VIEW_SIZE = 1600      # до какого размера читается фото для просмотра


class LargeView(tk.Toplevel):
    """Просмотр фото крупно: стрелки — листать, Esc — закрыть.

    Окно поднимается над тем, из которого его открыли: на рабочей машине окно редактора
    развёрнуто во весь экран, и просмотр, открывшийся позади него, выглядел как «двойной клик
    не работает». Фото с сетевого диска читается небыстро (15 МБ), поэтому окно показывается
    сразу с надписью «загрузка», а прочитанное фото держится в памяти: при изменении размера
    окна файл заново не читается.
    """

    def __init__(self, master, folder: str, photos: List[str], index: int, cache: PhotoCache):
        super().__init__(master)
        self.folder = folder
        self.photos = photos
        self.index = index
        self.cache = cache
        self._image = None                     # прочитанное фото (PIL)
        self._loaded: Optional[int] = None     # для какого кадра оно прочитано
        self._drawn = (0, 0)                   # размер окна, под который картинка уже нарисована
        self.geometry("900x700")
        self.label = ttk.Label(self, anchor="center")
        self.label.pack(fill="both", expand=True)
        self.caption = tk.StringVar()
        ttk.Label(self, textvariable=self.caption, anchor="center").pack(fill="x", pady=4)
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Left>", lambda e: self.step(-1))
        self.bind("<Right>", lambda e: self.step(1))
        self.bind("<Configure>", self._on_configure)
        self.transient(master)
        self.lift()
        self.focus_force()
        self.caption.set("%s — загрузка…" % self.photos[self.index])
        self.after_idle(self.show)

    def step(self, delta: int) -> None:
        self.index = (self.index + delta) % len(self.photos)
        self.show()

    def _on_configure(self, _event=None) -> None:
        """Перерисовка нужна только если размер окна и правда изменился."""
        if (self.winfo_width(), self.winfo_height()) != self._drawn:
            self.show()

    def _label(self) -> str:
        return "%s   (%d из %d)" % (self.photos[self.index], self.index + 1, len(self.photos))

    def show(self) -> None:
        name = self.photos[self.index]
        self.title(name)
        if self._loaded != self.index:
            self.caption.set("%s — загрузка…" % name)
            self.update_idletasks()
            try:
                # крупный просмотр читает фото в большем размере, чем миниатюра из кэша
                self._image = feat.load_image(os.path.join(self.folder, name), VIEW_SIZE)
            except Exception as exc:
                self._image = None
                self.caption.set("Не удалось открыть: %s" % exc)
                return
            self._loaded = self.index
        size = max(self.winfo_width() - 20, self.winfo_height() - 60, 200)
        self._drawn = (self.winfo_width(), self.winfo_height())
        self._photo = to_photo(self._image, size)
        self.label.configure(image=self._photo)
        self.caption.set(self._label())
