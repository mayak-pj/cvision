"""Первое окно программы: папка проекта → анализ → группы похожих папок и их эталоны.

Обработка идёт строго последовательно, без потоков. Окно не «замирает»: обратные вызовы
прогресса обновляют интерфейс (update()), а кнопка «Прервать» останавливает анализ.
"""

import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk
from typing import Dict, List, Optional

from .. import config as cfg
from .. import grouping
from ..cache import PhotoCache
from ..names import NamesList
from . import theme
from .editor import ReferenceEditor
from .review import BatchWindow

TITLE = "Переименование фото комплектующих"


class Cancelled(Exception):
    """Пользователь прервал анализ."""


def open_in_file_manager(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 — открыть папку в Проводнике
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class MainWindow(tk.Tk):
    def __init__(self, app_config: cfg.AppConfig, config_warnings: List[str] = ()):
        super().__init__()
        self.app_config = app_config
        self.names = NamesList(app_config.names_path())
        self.analysis: Optional[grouping.ProjectAnalysis] = None
        self.project_root = ""
        self._busy = False
        self._cancel = False
        self._close_requested = False
        self._item_folder: Dict[str, grouping.FolderInfo] = {}
        self._item_group: Dict[str, int] = {}

        theme.apply(self, app_config.theme)
        self.palette = theme.colors()
        self.title(TITLE)
        self.geometry("1150x650")
        self.minsize(850, 450)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build()
        self.root_var.set(self._load_state().get("project", ""))
        self._update_buttons()
        if config_warnings:
            messagebox.showwarning(TITLE, "Замечания к настройкам (config.json):\n\n" + "\n".join(config_warnings),
                                   parent=self)

    # --- построение окна ------------------------------------------------------------------

    def _build(self) -> None:
        top = ttk.Frame(self, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Папка проекта:").pack(side="left")
        self.root_var = tk.StringVar()
        self.root_entry = ttk.Entry(top, textvariable=self.root_var)
        self.root_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.root_entry.bind("<Return>", lambda e: self.analyze())
        self.browse_btn = ttk.Button(top, text="Обзор…", command=self.browse)
        self.browse_btn.pack(side="left")
        self.analyze_btn = ttk.Button(top, text="Анализировать", command=self.analyze)
        self.analyze_btn.pack(side="left", padx=(6, 0))

        hint = ("Программа находит все папки с фото, сравнивает их и объединяет похожие в группы. "
                "Для каждой группы нужен один эталон — папка, переименованная по шаблону.")
        ttk.Label(self, text=hint, foreground=self.palette["muted"], padding=(10, 0, 10, 6),
                  wraplength=1100).pack(fill="x")

        middle = ttk.Frame(self, padding=(10, 0, 10, 0))
        middle.pack(fill="both", expand=True)

        tree_frame = ttk.Frame(middle)
        tree_frame.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=("count", "status", "similarity"), selectmode="browse")
        self.tree.heading("#0", text="Группа / папка")
        self.tree.heading("count", text="Фото")
        self.tree.heading("status", text="Статус")
        self.tree.heading("similarity", text="Сходство с эталоном")
        self.tree.column("#0", width=430)
        self.tree.column("count", width=60, anchor="e", stretch=False)
        self.tree.column("status", width=260)
        self.tree.column("similarity", width=150, stretch=False)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_buttons())
        self.tree.bind("<Double-1>", lambda e: self.open_folder())

        bold = font.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        self.tree.tag_configure("group", font=bold)
        self.tree.tag_configure("reference", font=bold)
        self.tree.tag_configure("not_ready", foreground=self.palette["warning"])
        self.tree.tag_configure("low", foreground=self.palette["error"])

        side = ttk.Frame(middle, padding=(10, 0, 0, 0))
        side.pack(side="left", fill="y")
        ttk.Label(side, text="Выбранная папка").pack(anchor="w", pady=(0, 4))
        self.reference_btn = ttk.Button(side, text="Сделать эталоном группы", command=self.make_reference)
        self.reference_btn.pack(fill="x", pady=2)
        self.move_btn = ttk.Menubutton(side, text="Перенести в группу ▾")
        self.move_menu = tk.Menu(self.move_btn, tearoff=False, postcommand=self._fill_move_menu)
        self.move_btn["menu"] = self.move_menu
        self.move_btn.pack(fill="x", pady=2)
        self.open_btn = ttk.Button(side, text="Открыть папку", command=self.open_folder)
        self.open_btn.pack(fill="x", pady=2)
        ttk.Separator(side).pack(fill="x", pady=10)
        ttk.Label(side, text="Группа").pack(anchor="w", pady=(0, 4))
        self.edit_reference_btn = ttk.Button(side, text="Создать / изменить эталон…", command=self.edit_reference)
        self.edit_reference_btn.pack(fill="x", pady=2)
        self.rename_group_btn = ttk.Button(side, text="Переименовать папки группы…", command=self.rename_group)
        self.rename_group_btn.pack(fill="x", pady=2)
        ttk.Separator(side).pack(fill="x", pady=10)
        self.refresh_btn = ttk.Button(side, text="Обновить имена файлов", command=self.refresh)
        self.refresh_btn.pack(fill="x", pady=2)

        bottom = ttk.Frame(self, padding=(10, 6, 10, 10))
        bottom.pack(fill="x")
        self.status_var = tk.StringVar(value="Выберите папку проекта и нажмите «Анализировать».")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left", fill="x", expand=True)
        self.cancel_btn = ttk.Button(bottom, text="Прервать", command=self.cancel)
        self.progress = ttk.Progressbar(bottom, length=260, mode="determinate")

    # --- состояние между запусками ----------------------------------------------------------

    def _state_path(self) -> str:
        return os.path.join(os.path.dirname(self.app_config.cache_path()), "state.json")

    def _load_state(self) -> dict:
        try:
            with open(self._state_path(), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._state_path()), exist_ok=True)
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump({"project": self.root_var.get().strip()}, f, ensure_ascii=False)
        except OSError:
            pass

    # --- анализ -------------------------------------------------------------------------------

    def browse(self) -> None:
        path = filedialog.askdirectory(parent=self, title="Папка проекта", initialdir=self.root_var.get() or None)
        if path:
            self.root_var.set(os.path.normpath(path))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._cancel = False
        state = "disabled" if busy else "normal"
        for widget in (self.root_entry, self.browse_btn, self.analyze_btn):
            widget.configure(state=state)
        if busy:
            self.progress.pack(side="left", padx=6)
            self.cancel_btn.pack(side="left")
        else:
            self.progress.pack_forget()
            self.cancel_btn.pack_forget()
        self._update_buttons()

    def _pump(self) -> None:
        self.update()
        if self._cancel:
            raise Cancelled()

    def _on_folder(self, n: int, total: int, path: str) -> None:
        self.status_var.set("Папка %d из %d: %s" % (n, total, self._display_path(path)))
        self.progress.configure(maximum=1, value=0)
        self._pump()

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.configure(maximum=max(total, 1), value=done)
        self._pump()

    def cancel(self) -> None:
        self._cancel = True

    def analyze(self) -> None:
        if self._busy:
            return
        root = self.root_var.get().strip()
        if not root or not os.path.isdir(root):
            messagebox.showerror(TITLE, "Папка не найдена:\n%s" % (root or "(не указана)"), parent=self)
            return
        self.project_root = root
        self._save_state()
        self._set_busy(True)
        self.status_var.set("Поиск папок с фото…")
        self._pump_safe()
        cache = PhotoCache(self.app_config.cache_path(), self.app_config.cache_max_mb)
        try:
            self.analysis = grouping.analyze_project(
                [root], self.app_config.extractor(), self.app_config.match.weights,
                self.app_config.group_threshold, feature_source=cache.extract_many, sort=self.app_config.sort,
                date_source=cache.shot_time,
                on_folder=self._on_folder, on_progress=self._on_progress)
        except Cancelled:
            self.status_var.set("Анализ прерван.")
        except Exception as exc:  # показать любую ошибку, а не закрыть программу
            self.status_var.set("Ошибка анализа.")
            messagebox.showerror(TITLE, "Ошибка при анализе:\n%s" % exc, parent=self)
        else:
            self.status_var.set(self._summary())
        finally:
            cache.close()
            self._set_busy(False)
        self.render()
        if self._close_requested:
            self.destroy()

    def _pump_safe(self) -> None:
        try:
            self._pump()
        except Cancelled:
            pass

    def _summary(self) -> str:
        a = self.analysis
        need = sum(not g.reference_is_ready for g in a.groups)
        text = "Папок с фото: %d, групп: %d. " % (len(a.folders), len(a.groups))
        text += ("Нужно создать эталонов: %d." % need) if need else "Все эталоны готовы."
        if a.errors:
            text += " Недоступных папок: %d." % len(a.errors)
        return text

    # --- отображение ------------------------------------------------------------------------

    def _display_path(self, path: str) -> str:
        root = self.project_root
        if root:
            try:
                rel = os.path.relpath(path, root)
            except ValueError:  # другой диск на Windows
                return path
            if not rel.startswith(".."):
                return os.path.join(os.path.basename(os.path.normpath(root)), rel) if rel != "." else \
                    os.path.basename(os.path.normpath(root))
        return path

    def _folder_status(self, group: grouping.FolderGroup, folder: grouping.FolderInfo) -> str:
        if folder is group.reference:
            status = "ЭТАЛОН — готов" if folder.is_reference else "ЭТАЛОН — нужно переименовать вручную"
        elif folder.is_reference:
            status = "уже названа по шаблону"
        else:
            status = ""
        if folder.unreadable:
            status += ("; " if status else "") + "не прочитано фото: %d" % folder.unreadable
        return status

    def render(self) -> None:
        selected = self._selected_folder()
        self.tree.delete(*self.tree.get_children())
        self._item_folder.clear()
        self._item_group.clear()
        if self.analysis is None:
            self._update_buttons()
            return
        for k, group in enumerate(self.analysis.groups):
            ready = "эталон готов" if group.reference_is_ready else "нужно создать эталон"
            gid = self.tree.insert("", "end", text="Группа %d — %s" % (k + 1, ready),
                                   values=("", "папок: %d" % len(group.folders), ""), open=True,
                                   tags=("group",) if group.reference_is_ready else ("group", "not_ready"))
            self._item_group[gid] = k
            for folder in group.folders:
                similarity = grouping.similarity_label(grouping.similarity_to_reference(self.analysis, folder))
                tags = []
                if folder is group.reference:
                    tags.append("reference")
                    if not folder.is_reference:
                        tags.append("not_ready")
                if similarity == "низкое":
                    tags.append("low")
                iid = self.tree.insert(gid, "end", text=self._display_path(folder.path),
                                       values=(folder.count, self._folder_status(group, folder), similarity),
                                       tags=tuple(tags))
                self._item_folder[iid] = folder
                self._item_group[iid] = k
                if folder is selected:
                    self.tree.selection_set(iid)
                    self.tree.see(iid)
        for e in self.analysis.errors:
            self.tree.insert("", "end", text="Недоступна: %s" % self._display_path(e.path), values=("", e.message, ""),
                             tags=("low",))
        self._update_buttons()

    # --- действия ------------------------------------------------------------------------------

    def _selected_item(self) -> Optional[str]:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _selected_folder(self) -> Optional[grouping.FolderInfo]:
        item = self._selected_item()
        return self._item_folder.get(item) if item else None

    def _selected_group(self) -> Optional[int]:
        item = self._selected_item()
        return self._item_group.get(item) if item else None

    def _update_buttons(self) -> None:
        folder = None if self._busy else self._selected_folder()
        group = None if self._busy else self._selected_group()
        is_ref = folder is not None and self.analysis.groups[group].reference is folder
        self.reference_btn.configure(state="normal" if folder is not None and not is_ref else "disabled")
        self.move_btn.configure(state="normal" if folder is not None else "disabled")
        self.open_btn.configure(state="normal" if folder is not None else "disabled")
        self.edit_reference_btn.configure(state="normal" if group is not None else "disabled")
        can_rename = group is not None and self.analysis.groups[group].reference_is_ready
        self.rename_group_btn.configure(state="normal" if can_rename else "disabled")
        self.refresh_btn.configure(state="normal" if self.analysis is not None and not self._busy else "disabled")

    def _fill_move_menu(self) -> None:
        self.move_menu.delete(0, "end")
        folder = self._selected_folder()
        if folder is None:
            return
        current = grouping.group_of(self.analysis, folder)
        for k, group in enumerate(self.analysis.groups):
            label = "Группа %d (папок: %d)" % (k + 1, len(group.folders))
            self.move_menu.add_command(label=label, state="disabled" if k == current else "normal",
                                       command=lambda k=k: self.move_selected(k))
        self.move_menu.add_separator()
        self.move_menu.add_command(label="Новая группа", command=lambda: self.move_selected(None))

    def move_selected(self, target: Optional[int]) -> None:
        folder = self._selected_folder()
        if folder is None:
            return
        grouping.move_folder(self.analysis, folder, target)
        self.render()
        self.status_var.set(self._summary())

    def make_reference(self) -> None:
        folder = self._selected_folder()
        if folder is None:
            return
        grouping.set_reference(self.analysis, folder)
        self.render()
        self.status_var.set(self._summary())

    def open_folder(self) -> None:
        folder = self._selected_folder()
        if folder is not None:
            try:
                open_in_file_manager(folder.path)
            except OSError as exc:
                messagebox.showerror(TITLE, "Не удалось открыть папку:\n%s" % exc, parent=self)

    def refresh(self) -> None:
        if self.analysis is None:
            return
        cache = PhotoCache(self.app_config.cache_path(), self.app_config.cache_max_mb)
        try:
            grouping.refresh_names(self.analysis, sort=self.app_config.sort, date_source=cache.shot_time)
        finally:
            cache.close()
        self.render()
        self.status_var.set(self._summary())

    def edit_reference(self) -> None:
        group = self._selected_group()
        if group is None:
            return
        folder = self.analysis.groups[group].reference
        ReferenceEditor(self, folder.path, self.app_config, self.names, on_done=self.refresh)

    def rename_group(self) -> None:
        group = self._selected_group()
        if group is None:
            return
        target = self.analysis.groups[group]
        if not target.targets:
            messagebox.showinfo(TITLE, "В этой группе нет папок для переименования.", parent=self)
            return
        BatchWindow(self, target, self.app_config, on_done=self.refresh)

    def on_close(self) -> None:
        if self._busy:
            self._close_requested = True
            self._cancel = True
            return
        self.destroy()


def main(argv=None) -> None:
    config, warnings = cfg.load_config()
    MainWindow(config, warnings).mainloop()
