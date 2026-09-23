"""Просмотр фото крупно. Одно окно на редактор эталона и на пакетное переименование."""

import os
from typing import List

import tkinter as tk
from tkinter import ttk

from .. import features as feat
from ..cache import PhotoCache
from .thumbgrid import to_photo


class LargeView(tk.Toplevel):
    """Просмотр фото крупно: стрелки — листать, Esc — закрыть."""

    def __init__(self, master, folder: str, photos: List[str], index: int, cache: PhotoCache):
        super().__init__(master)
        self.folder = folder
        self.photos = photos
        self.index = index
        self.cache = cache
        self.geometry("900x700")
        self.label = ttk.Label(self, anchor="center")
        self.label.pack(fill="both", expand=True)
        self.caption = tk.StringVar()
        ttk.Label(self, textvariable=self.caption, anchor="center").pack(fill="x", pady=4)
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Left>", lambda e: self.step(-1))
        self.bind("<Right>", lambda e: self.step(1))
        self.bind("<Configure>", lambda e: self.show())
        self.focus_set()
        self.show()

    def step(self, delta: int) -> None:
        self.index = (self.index + delta) % len(self.photos)
        self.show()

    def show(self) -> None:
        name = self.photos[self.index]
        self.title(name)
        self.caption.set("%s   (%d из %d)" % (name, self.index + 1, len(self.photos)))
        try:
            # крупный просмотр читает фото в большем размере, чем миниатюра из кэша
            image = feat.load_image(os.path.join(self.folder, name), 1600)
        except Exception as exc:
            self.caption.set("Не удалось открыть: %s" % exc)
            return
        size = max(self.winfo_width() - 20, self.winfo_height() - 60, 200)
        self._photo = to_photo(image, size)
        self.label.configure(image=self._photo)
