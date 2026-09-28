"""Первое окно: анализ проекта, дерево групп, действия. Окно создаётся скрытым."""

import os

import pytest

tk = pytest.importorskip("tkinter")

from photo_renamer import config as cfg  # noqa: E402
from photo_renamer.gui import app as gui_app  # noqa: E402
from test_grouping import shoot  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch):
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(gui_app.messagebox, name, lambda *a, **k: None)
    config = cfg.AppConfig()
    config.cache_dir = str(tmp_path / "cache")
    config.logs_dir = str(tmp_path / "logs")
    try:
        win = gui_app.MainWindow(config)
    except tk.TclError as exc:
        pytest.skip("нет графической среды: %s" % exc)
    win.withdraw()
    yield win
    win.destroy()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "Проект"
    ref_names = ["Кулер.JPG", "Процессор 1 фото 1.JPG", "Процессор 1 фото 2.JPG", "Кабель HDMI.JPG",
                 "Радиатор 1.JPG", "Радиатор 2.JPG"]
    shoot(root / "A" / "эталон", [1, 2, 3, 4, 5, 6], names=ref_names)
    shoot(root / "A" / "SN2", [1, 2, 3, 4, 5, 6])
    shoot(root / "B" / "SN4", [11, 12, 13, 14, 15, 16, 17])
    shoot(root / "B" / "SN5", [11, 12, 13, 14, 15, 16, 17, 18])
    return root


def folder_rows(win):
    rows = []
    for gid in win.tree.get_children():
        for iid in win.tree.get_children(gid):
            rows.append((win._item_group[iid], win.tree.item(iid, "text"), tuple(win.tree.item(iid, "values"))))
    return rows


def test_analyze_and_render(window, project):
    window.root_var.set(str(project))
    window.analyze()
    window.wait_for_worker()
    assert window.analysis is not None and not window._busy
    groups = window.tree.get_children()
    assert len(groups) == 2
    assert "эталон готов" in window.tree.item(groups[0], "text")
    assert "нужно создать эталон" in window.tree.item(groups[1], "text")
    rows = folder_rows(window)
    assert (0, os.path.join("Проект", "A", "эталон"),
            (gui_app.MARK_REFERENCE, "", "6", "100% переименована", "")) in rows
    assert any(r[1].endswith("SN5") and r[2][0] == gui_app.MARK_REFERENCE
               and r[2][3] == "0% переименована" for r in rows)
    assert any(r[1].endswith("SN2") and r[2][:2] == ("", "да") for r in rows)  # папка будет переименована
    assert "Нужно создать эталонов: 1" in window.status_var.get()
    # папка проекта запоминается
    assert window._load_state()["project"] == str(project)


def select(window, suffix):
    for iid, folder in window._item_folder.items():
        if folder.path.endswith(suffix):
            window.tree.selection_set(iid)
            window._update_buttons()
            return folder
    raise AssertionError(suffix)


def test_buttons_follow_selection(window, project):
    window.root_var.set(str(project))
    window.analyze()
    window.wait_for_worker()
    assert str(window.reference_btn["state"]) == "disabled"
    select(window, "SN2")
    assert str(window.reference_btn["state"]) == "normal"
    assert str(window.rename_group_btn["state"]) == "normal"     # в группе готовый эталон
    select(window, "SN4")
    assert str(window.rename_group_btn["state"]) == "disabled"   # эталон ещё не создан


def test_make_reference_and_move(window, project):
    window.root_var.set(str(project))
    window.analyze()
    window.wait_for_worker()
    folder = select(window, "SN4")
    window.make_reference()
    assert window.analysis.groups[1].reference is folder
    select(window, "SN2")
    window.move_selected(None)                                   # в новую группу
    assert len(window.tree.get_children()) == 3
    select(window, "SN2")
    window.move_selected(0)                                      # обратно
    assert len(window.tree.get_children()) == 2


def test_refresh_after_manual_rename(window, project):
    window.root_var.set(str(project))
    window.analyze()
    window.wait_for_worker()
    folder = select(window, "SN5")
    names = ["Кулер.JPG", "Вентилятор 1.JPG", "Вентилятор 2.JPG", "Кабель HDMI.JPG", "Радиатор 1.JPG",
             "Радиатор 2.JPG", "Плата фото 1.JPG", "Плата фото 2.JPG"]
    for old, new in zip(sorted(os.listdir(folder.path)), names):
        os.rename(os.path.join(folder.path, old), os.path.join(folder.path, new))
    window.refresh()
    assert "Все эталоны готовы" in window.status_var.get()


def test_missing_folder_and_cancel(window, tmp_path, project):
    window.root_var.set(str(tmp_path / "нет"))
    window.analyze()
    window.wait_for_worker()
    assert window.analysis is None
    window.root_var.set(str(project))
    original = window._on_worker_message

    def cancel_on_first(message):      # прерываем, как только пошло чтение фото
        window.cancel()
        original(message)

    window._on_worker_message = cancel_on_first
    window.analyze()
    window.wait_for_worker()
    assert window.status_var.get() == "Анализ прерван." and not window._busy


def test_window_stays_responsive_during_analysis(window, project, monkeypatch):
    """Чтение фото идёт в фоновой задаче: окно отвечает на действия, пока идёт анализ."""
    import time

    from photo_renamer.gui import app as app_module

    real = app_module.PhotoCache

    class SlowCache(real):
        def extract_many(self, extractor, paths, on_progress=None):
            time.sleep(0.2)            # медленное чтение, как по сети
            return super().extract_many(extractor, paths, on_progress)

    monkeypatch.setattr(app_module, "PhotoCache", SlowCache)
    window.root_var.set(str(project))
    window.analyze()
    assert window.worker.busy()

    # во время анализа активна кнопка «Прервать» — она и должна отвечать сразу
    clicks = []
    window.cancel_btn.configure(command=lambda: clicks.append(1))
    started = time.perf_counter()
    window.cancel_btn.event_generate("<Button-1>")
    window.cancel_btn.event_generate("<ButtonRelease-1>")
    while not clicks and time.perf_counter() - started < 5:
        window.update()
    assert clicks, "кнопка не ответила, пока шёл анализ"
    assert time.perf_counter() - started < 0.2, "ответ занял больше 200 мс"

    window.wait_for_worker()
    assert window.analysis is not None


def test_lamp_switches_theme_and_remembers(window, tmp_path, monkeypatch):
    from photo_renamer import config as config_module
    from photo_renamer.gui import theme

    saved = []
    monkeypatch.setattr(config_module, "save_config", lambda cfg_, path=None: saved.append(cfg_.theme))
    monkeypatch.setattr(gui_app.cfg, "save_config", lambda cfg_, path=None: saved.append(cfg_.theme))

    assert window.lamp.on and theme.current_mode() == "light"
    window.lamp.toggle()                     # погасили лампочку — тёмное оформление
    assert not window.lamp.on
    assert theme.current_mode() == "dark"
    assert window.app_config.theme == "dark" and saved == ["dark"]
    assert window.cget("background") == theme.DARK["background"]

    window.lamp.toggle()
    assert theme.current_mode() == "light" and window.app_config.theme == "light"
    theme.apply(window, "light")


def test_rename_mark_can_be_switched(window, project):
    """Отметку «переименовывать» ставит человек: программа могла счесть папку уже названной."""
    window.root_var.set(str(project))
    window.analyze()
    window.wait_for_worker()
    folder = select(window, "SN2")
    group = window.analysis.groups[0]
    assert folder in group.targets and folder.will_rename

    window.toggle_rename(folder)
    assert not folder.will_rename and folder not in group.targets
    assert any(r[1].endswith("SN2") and r[2][1] == "нет" for r in folder_rows(window))

    window.toggle_rename(folder)
    assert folder.will_rename and folder in group.targets


def test_reference_editor_opens_the_selected_folder(window, project, monkeypatch):
    """Кнопка «Создать / изменить эталон» открывает выделенную папку, а не эталон группы."""
    opened = []
    monkeypatch.setattr(gui_app, "ReferenceEditor", lambda master, path, *a, **k: opened.append(path))
    window.root_var.set(str(project))
    window.analyze()
    window.wait_for_worker()
    folder = select(window, "SN2")
    window.edit_reference()
    assert opened == [folder.path]


def test_dragging_a_column_does_not_move_the_others(window):
    """Замечание с рабочей машины: столбцы «неестественно уезжают».

    Tk забирает разницу у растяжимого столбца, поэтому при перетаскивании границы «Фото»
    ужималась «Группа / папка», которую никто не трогал.
    """
    window.deiconify()
    window.geometry("1100x600")
    window.update()
    tree = window.tree
    columns = ("#0",) + tree.cget("columns")
    before = {name: tree.column(name, "width") for name in columns}

    border = before["#0"] + before["reference"] + before["rename"] + before["count"]
    tree.event_generate("<ButtonPress-1>", x=border, y=8, when="now")
    tree.event_generate("<B1-Motion>", x=border + 60, y=8, when="now")
    tree.event_generate("<ButtonRelease-1>", x=border + 60, y=8, when="now")
    window.update()

    after = {name: tree.column(name, "width") for name in columns}
    assert after["count"] > before["count"], "столбец, который тянули, не изменился"
    # последний столбец — добивка: он забирает свободное место, чтобы справа не было пустоты
    untouched = [name for name in columns if name not in ("count", "similarity")]
    assert {name: after[name] for name in untouched} == \
           {name: before[name] for name in untouched}


def test_horizontal_scrollbar_appears_only_when_needed(window):
    """Полоса показана ровно тогда, когда столбцы не помещаются — при любом шрифте."""
    from photo_renamer.gui import theme

    window.deiconify()
    for width in (1400, 2200):
        window.geometry("%dx600" % width)
        window.update()
        window._update_hscroll()
        window.update()
        fits = theme.columns_width(window.tree) <= window.tree.winfo_width()
        assert bool(window.hscroll.winfo_ismapped()) != fits, width

    window.tree.column("status", width=2000)       # заведомо не помещается
    window._update_hscroll()
    window.update()
    assert window.hscroll.winfo_ismapped(), "столбцы не помещаются — нужна полоса прокрутки"


def test_columns_are_not_narrower_than_their_headings(window):
    """Подписи столбцов не должны обрезаться — на рабочей машине шрифт крупнее."""
    from tkinter import font

    window.update()
    heading_font = font.nametofont("TkHeadingFont")
    for name in ("#0",) + window.tree.cget("columns"):
        title = window.tree.heading(name, "text")
        assert window.tree.column(name, "width") >= heading_font.measure(title), title


def test_every_column_reads_from_the_left(window):
    for name in window.tree.cget("columns"):
        assert str(window.tree.column(name, "anchor")) == "w", name
        assert str(window.tree.heading(name, "anchor")) == "w", name


def test_partly_renamed_folder_is_marked_and_renamed(window, project):
    """Недоделанную папку программа переименовывает, и в статусе видно, насколько она готова."""
    window.root_var.set(str(project))
    window.analyze()
    window.wait_for_worker()
    group = window.analysis.groups[0]
    target = [f for f in group.folders if f is not group.reference][0]

    target.named_share = 20 / 24.0      # как будто пару десятков кадров переименовали руками
    target.coverage = 0.2
    assert window._folder_status(group, target) == "83% переименована"
    assert target.will_rename and target in group.targets


def test_hundred_percent_means_every_photo(window, project):
    """99.6 % — это ещё не готово, поэтому 100 пишется только когда названы все."""
    window.root_var.set(str(project))
    window.analyze()
    window.wait_for_worker()
    group = window.analysis.groups[0]
    folder = group.folders[0]

    folder.named_share = 0.996
    assert window._folder_status(group, folder) == "99% переименована"
    folder.named_share = 1.0
    assert window._folder_status(group, folder) == "100% переименована"


def test_reference_mark_starts_on_the_most_renamed_folder(window, project):
    """Галочка эталона изначально стоит на папке с самым высоким процентом."""
    window.root_var.set(str(project))
    window.analyze()
    window.wait_for_worker()
    for group in window.analysis.groups:
        best = max(group.folders, key=lambda f: (f.named_share, f.count))
        assert group.reference.named_share == best.named_share
    marks = {r[1]: r[2][0] for r in folder_rows(window)}
    for group in window.analysis.groups:
        shown = window._display_path(group.reference.path)
        assert marks[shown] == gui_app.MARK_REFERENCE


def test_no_empty_space_to_the_right_of_the_columns(window):
    """Замечание с мака: справа в таблице оставалась ничья пустая полоса."""
    from photo_renamer.gui import theme

    window.deiconify()
    for width in (900, 1400, 1800, 2200):
        window.geometry("%dx700" % width)
        window.update()
        # проверяем правило, а не числа: свободного места справа не бывает никогда —
        # столбцы либо занимают всю ширину, либо не помещаются (и тогда есть полоса)
        assert theme.columns_width(window.tree) >= window.tree.winfo_width(), width


def test_side_panel_keeps_its_buttons_readable(window):
    """И там же: надписи на кнопках справа обрезались («Сделать эталоном груг»)."""
    window.deiconify()
    buttons = (window.reference_btn, window.move_btn, window.open_btn,
               window.edit_reference_btn, window.rename_group_btn, window.refresh_btn)
    for width in (1100, 1400, 2200):
        window.geometry("%dx700" % width)
        window.update()
        cut = [b.cget("text") for b in buttons if b.winfo_width() < b.winfo_reqwidth()]
        assert not cut, "при ширине %d обрезаны: %s" % (width, cut)


def test_hint_wraps_by_the_window_width(window):
    """Подсказка вверху не должна уходить за край узкого окна."""
    window.deiconify()
    for width in (900, 1600):
        window.geometry("%dx700" % width)
        window.update()
        assert window.hint_label.cget("wraplength") <= width
        assert window.hint_label.winfo_reqwidth() <= width


def test_all_columns_fit_when_the_window_opens(window):
    """Замечание с мака: «Сходство» не влезало при открытии, внизу висела полоса прокрутки."""
    from photo_renamer.gui import theme

    window.deiconify()
    window.update()
    # окно раздвигается под столбцы, но шире экрана не становится: на узком экране
    # с крупным шрифтом они могут и не поместиться — тогда должна быть полоса прокрутки
    room = int(window.winfo_screenwidth() * 0.95) - window.winfo_width()
    if theme.columns_width(window.tree) > window.tree.winfo_width():
        assert room <= 0, "окно можно было раздвинуть, но столбцы не поместились"
        assert window.hscroll.winfo_ismapped()
    else:
        assert not window.hscroll.winfo_ismapped()


def test_status_column_is_sized_by_its_longest_value(window):
    """«Статус» больше не занимает место зря: ширина — по «100% переименована»."""
    from tkinter import font

    text = font.nametofont("TkDefaultFont")
    width = window.tree.column("status", "width")
    assert width >= text.measure("100% переименована")
    assert width <= text.measure("100% переименована") + 40


def test_dropdown_caption_is_centred_like_other_buttons(window):
    """«Перенести в группу» выбивалась из ряда: в clam подпись прижата к левому краю."""
    from tkinter import ttk

    assert window.move_btn.cget("style") == "Centered.TMenubutton"
    assert "▾" not in window.move_btn.cget("text"), "стрелку рисует сам стиль"
    layout = ttk.Style(window).layout("Centered.TMenubutton")

    def label_options(items):
        for name, options in items:
            if name == "Menubutton.label":
                return options
            found = label_options(options.get("children", []))
            if found is not None:
                return found
        return None

    assert label_options(layout).get("expand"), "подпись должна занимать всю кнопку"
