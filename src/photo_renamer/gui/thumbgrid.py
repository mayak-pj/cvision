"""Сетка миниатюр с выделением как в Проводнике.

- размер миниатюр меняется Ctrl+колёсиком (от 100 до 400 px) и кнопками;
- выделение: щелчок, Ctrl+щелчок, Shift+щелчок, рамка мышью, Ctrl+A, Esc;
- двойной щелчок — просмотр фото крупно;
- миниатюры подгружаются постепенно (по одной за такт), поэтому окно не «замирает».

Изображения отдаются в Tk через PNG (Tk 8.6 умеет его сам), а не через PIL.ImageTk:
меньше зависимостей и нет привязки Pillow к конкретной сборке Tcl/Tk.
"""

import base64
import io
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional, Sequence, Tuple

LOAD_BUDGET = 0.05   # столько секунд подряд грузим миниатюры, затем отдаём время событиям
LOAD_PAUSE = 15      # пауза между пачками, мс: за неё обрабатываются щелчки и прокрутка

GAP = 12
LABEL_HEIGHT = 34
MIN_SIZE, MAX_SIZE = 100, 400
SELECTED_COLOR = "#CCE4FF"
SELECTED_BORDER = "#3B82F6"
TEMPLATE_COLOR = "#166534"   # имя уже по шаблону
PLAIN_COLOR = "#111827"


def to_photo(image, size: int) -> tk.PhotoImage:
    """PIL-изображение → tk.PhotoImage через PNG (без PIL.ImageTk)."""
    thumb = image.copy()
    thumb.thumbnail((size, size))
    buffer = io.BytesIO()
    thumb.save(buffer, "PNG")
    return tk.PhotoImage(data=base64.b64encode(buffer.getvalue()))


class ThumbnailGrid(ttk.Frame):
    def __init__(self, master, load_thumbnail: Callable[[str], object], cell_size: int = 160,
                 on_selection_change: Optional[Callable[[], None]] = None,
                 on_activate: Optional[Callable[[int], None]] = None,
                 on_load_progress: Optional[Callable[[int, int], None]] = None, **kwargs):
        super().__init__(master, **kwargs)
        self.load_thumbnail = load_thumbnail
        self.cell_size = cell_size
        self.on_selection_change = on_selection_change
        self.on_activate = on_activate
        self.on_load_progress = on_load_progress

        self.items: List[Tuple[str, str]] = []      # (имя файла, полный путь) в порядке съёмки
        self.template: List[bool] = []              # имя уже по шаблону
        self.selection: set = set()
        self._images: List[Optional[object]] = []   # PIL-изображения (миниатюры)
        self._photos: List[Optional[tk.PhotoImage]] = []
        self._columns = 1
        self._anchor: Optional[int] = None
        self._pending = 0
        self._loader = None
        self._drag: Optional[Tuple[int, int]] = None
        self._drag_rect = None
        self._drag_base: set = set()

        self.canvas = tk.Canvas(self, background="white", highlightthickness=0, takefocus=True)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

        self.canvas.bind("<Configure>", lambda e: self._layout())
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind(sequence, self._on_wheel)
        self.canvas.bind("<Control-a>", lambda e: self.select_all())
        self.canvas.bind("<Escape>", lambda e: self.clear_selection())

    # --- содержимое ------------------------------------------------------------------

    def set_items(self, items: Sequence[Tuple[str, str]], template: Sequence[bool] = ()) -> None:
        self.items = list(items)
        self.template = list(template) + [False] * (len(items) - len(template))
        self.selection.clear()
        self._images = [None] * len(self.items)
        self._photos = [None] * len(self.items)
        self._anchor = None
        self._pending = 0
        self._layout()
        self._start_loading()

    def update_names(self, items: Sequence[Tuple[str, str]], template: Sequence[bool] = ()) -> None:
        """Обновляет имена и пути после переименования, не перечитывая миниатюры."""
        self.items = list(items)
        self.template = list(template) + [False] * (len(items) - len(template))
        self._layout()

    def selected_names(self) -> List[str]:
        return [self.items[i][0] for i in sorted(self.selection)]

    def _apply_selection(self, new_selection: set) -> None:
        """Перерисовывает только те ячейки, у которых изменилось выделение (полная перерисовка
        сетки на каждый щелчок заметно тормозит на папках в сотни фото)."""
        changed = self.selection ^ new_selection
        self.selection = new_selection
        for index in changed:
            if 0 <= index < len(self.items):
                self._draw_cell(index)
        self._notify()

    def select_all(self) -> None:
        self._apply_selection(set(range(len(self.items))))

    def clear_selection(self) -> None:
        self._apply_selection(set())

    def set_selection(self, indexes: Sequence[int]) -> None:
        self._apply_selection({i for i in indexes if 0 <= i < len(self.items)})

    def set_cell_size(self, size: int) -> None:
        size = max(MIN_SIZE, min(MAX_SIZE, int(size)))
        if size != self.cell_size:
            self.cell_size = size
            self._photos = [None] * len(self.items)
            self._layout()

    # --- загрузка миниатюр -------------------------------------------------------------

    def _start_loading(self) -> None:
        if self._loader is not None:
            self.after_cancel(self._loader)
            self._loader = None
        self._pending = 0
        self._load_next()

    def _load_next(self) -> None:
        """Грузит миниатюры пачкой не дольше LOAD_BUDGET, затем обязательно отдаёт время событиям.

        Одно фото весит ~15 МБ, и его чтение по сети может занять секунды. Пауза между пачками
        нужна, чтобы щелчки и прокрутка обрабатывались, а не ждали окончания загрузки.
        """
        self._loader = None
        deadline = time.monotonic() + LOAD_BUDGET
        while True:
            while self._pending < len(self.items) and self._images[self._pending] is not None:
                self._pending += 1
            if self._pending >= len(self.items):
                if self.on_load_progress:
                    self.on_load_progress(len(self.items), len(self.items))
                return
            index = self._pending
            try:
                self._images[index] = self.load_thumbnail(self.items[index][1])
            except Exception:  # нечитаемое фото не должно ломать сетку
                self._images[index] = False
            self._pending += 1
            self._draw_cell(index)
            if self.on_load_progress:
                self.on_load_progress(self._pending, len(self.items))
            if time.monotonic() >= deadline:
                break
        self._loader = self.after(LOAD_PAUSE, self._load_next)

    # --- раскладка и рисование ---------------------------------------------------------

    def _cell_box(self, index: int) -> Tuple[int, int, int, int]:
        row, column = divmod(index, self._columns)
        x = GAP + column * (self.cell_size + GAP)
        y = GAP + row * (self.cell_size + LABEL_HEIGHT + GAP)
        return x, y, x + self.cell_size, y + self.cell_size + LABEL_HEIGHT

    def _layout(self) -> None:
        width = max(self.canvas.winfo_width(), self.cell_size + 2 * GAP)
        self._columns = max(1, (width - GAP) // (self.cell_size + GAP))
        self.canvas.delete("all")
        for index in range(len(self.items)):
            self._draw_cell(index, redraw=False)
        rows = (len(self.items) + self._columns - 1) // self._columns
        height = GAP + rows * (self.cell_size + LABEL_HEIGHT + GAP)
        self.canvas.configure(scrollregion=(0, 0, width, max(height, 1)))

    def _draw_cell(self, index: int, redraw: bool = True) -> None:
        if redraw:
            self.canvas.delete("cell%d" % index)
        x0, y0, x1, y1 = self._cell_box(index)
        tags = ("cell%d" % index, "cell")
        selected = index in self.selection
        self.canvas.create_rectangle(x0 - 4, y0 - 4, x1 + 4, y1 + 4,
                                     fill=SELECTED_COLOR if selected else "white",
                                     outline=SELECTED_BORDER if selected else "#E5E7EB", tags=tags)
        image = self._images[index] if index < len(self._images) else None
        if image:
            photo = self._photos[index]
            if photo is None:
                photo = to_photo(image, self.cell_size)
                self._photos[index] = photo
            self.canvas.create_image((x0 + x1) // 2, y0 + self.cell_size // 2, image=photo, tags=tags)
        else:
            text = "…" if image is None else "нет\nпревью"
            self.canvas.create_text((x0 + x1) // 2, y0 + self.cell_size // 2, text=text, fill="#9CA3AF", tags=tags)
        name = self.items[index][0]
        self.canvas.create_text((x0 + x1) // 2, y1 - LABEL_HEIGHT // 2, text=name, width=self.cell_size,
                                fill=TEMPLATE_COLOR if self.template[index] else PLAIN_COLOR, tags=tags)

    # --- мышь и клавиши ------------------------------------------------------------------

    def _index_at(self, event) -> Optional[int]:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for index in range(len(self.items)):
            x0, y0, x1, y1 = self._cell_box(index)
            if x0 - 4 <= x <= x1 + 4 and y0 - 4 <= y <= y1 + 4:
                return index
        return None

    def _on_press(self, event) -> None:
        self.canvas.focus_set()
        index = self._index_at(event)
        ctrl = bool(event.state & 0x0004) or bool(event.state & 0x0008 and self.tk.call("tk", "windowingsystem") == "aqua")
        shift = bool(event.state & 0x0001)
        if index is None:
            self._drag = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
            self._drag_base = set(self.selection) if ctrl or shift else set()
            if not (ctrl or shift):
                self.clear_selection()
            return
        if shift and self._anchor is not None:
            low, high = sorted((self._anchor, index))
            new_selection = set(range(low, high + 1)) | (self.selection if ctrl else set())
        elif ctrl:
            new_selection = self.selection ^ {index}
            self._anchor = index
        else:
            new_selection = {index}
            self._anchor = index
        self._apply_selection(new_selection)

    def _on_drag(self, event) -> None:
        if self._drag is None:
            return
        x0, y0 = self._drag
        x1, y1 = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if self._drag_rect is not None:
            self.canvas.delete(self._drag_rect)
        self._drag_rect = self.canvas.create_rectangle(x0, y0, x1, y1, outline=SELECTED_BORDER, dash=(3, 2))
        box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        inside = set()
        for index in range(len(self.items)):
            cx0, cy0, cx1, cy1 = self._cell_box(index)
            if not (cx1 < box[0] or cx0 > box[2] or cy1 < box[1] or cy0 > box[3]):
                inside.add(index)
        self._apply_selection(self._drag_base | inside)
        self.canvas.tag_raise(self._drag_rect)

    def _on_release(self, event) -> None:
        if self._drag_rect is not None:
            self.canvas.delete(self._drag_rect)
            self._drag_rect = None
        self._drag = None

    def _on_double(self, event) -> None:
        index = self._index_at(event)
        if index is not None and self.on_activate:
            self.on_activate(index)

    def _on_wheel(self, event) -> None:
        delta = event.delta if event.delta else (120 if getattr(event, "num", 0) == 4 else -120)
        if event.state & 0x0004:  # Ctrl — масштаб
            self.set_cell_size(self.cell_size + (20 if delta > 0 else -20))
            return
        step = -1 if delta > 0 else 1
        self.canvas.yview_scroll(step * (1 if abs(delta) < 120 else 3), "units")

    def _notify(self) -> None:
        if self.on_selection_change:
            self.on_selection_change()
