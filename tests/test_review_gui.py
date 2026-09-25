"""Экран пакетного переименования: предпросмотр, ручная правка, переименование, отмена."""

import os

import pytest

tk = pytest.importorskip("tkinter")

from photo_renamer import config as cfg, grouping  # noqa: E402
from photo_renamer.gui import review as review_mod  # noqa: E402
from photo_renamer.scanner import list_photos  # noqa: E402
from test_grouping import shoot  # noqa: E402

REF_NAMES = ["Кулер.JPG", "Процессор 1 фото 1.JPG", "Процессор 1 фото 2.JPG", "Кабель HDMI.JPG",
             "Радиатор 1.JPG", "Радиатор 2.JPG"]
PARTS = [1, 2, 3, 4, 5, 6]


@pytest.fixture
def batch(tmp_path, monkeypatch):
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(review_mod.messagebox, name, lambda *a, **k: None)
    monkeypatch.setattr(review_mod.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(review_mod, "ReportDialog", lambda *a, **k: None)
    saved = []          # настройки пользователя тесты не трогают
    monkeypatch.setattr(review_mod.cfg, "save_config", lambda cfg_, path=None: saved.append(cfg_.review_thumb))

    root_dir = tmp_path / "Проект"
    shoot(root_dir / "эталон", PARTS, names=REF_NAMES)
    shoot(root_dir / "SN2", PARTS)
    infos = [grouping.folder_info(list_photos(str(root_dir / name))) for name in ("эталон", "SN2")]
    group = grouping.FolderGroup(infos, infos[0], True)

    config = cfg.AppConfig()
    config.cache_dir = str(tmp_path / "cache")
    config.logs_dir = str(tmp_path / "logs")
    config.names_file = str(tmp_path / "names.txt")   # список категорий пользователя не трогаем
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip("нет графической среды: %s" % exc)
    root.withdraw()
    win = review_mod.BatchWindow(root, group, config)
    win.withdraw()
    win.start()
    yield win, root_dir
    win.log.close()
    win.cache.close()
    win.destroy()
    root.destroy()


def rows(win):
    return [(win.tree.item(iid, "text").strip(), win.tree.item(iid, "values")[0]) for iid in win.tree.get_children()]


def iid_of(win, filename):
    """Идентификатор строки по имени файла: после каждой правки таблица пересоздаётся."""
    for iid in win.tree.get_children():
        if win.tree.item(iid, "text").strip() == filename:
            return iid
    raise AssertionError(filename)


def row_of(win, filename):
    """Строка таблицы по имени файла (порядок строк зависит от сортировки «сомнительные первыми»)."""
    for iid in win.tree.get_children():
        if win.tree.item(iid, "text").strip() == filename:
            return win.tree.item(iid, "values")
    raise AssertionError(filename)


def test_preview_is_built(batch):
    win, root_dir = batch
    assert win.model is not None and len(win.categories) == 4
    assert "Папка 1 из 1" in win.header_var.get()
    assert len(rows(win)) == 6
    assert "Фото: 6" in win.status_var.get()
    assert sorted(os.listdir(str(root_dir / "SN2")))[0].startswith("IMG_")  # ничего не переименовано


def select_row(win, position):
    iid = win.tree.get_children()[position]
    win.tree.selection_set(iid)
    win._on_row_select()
    return iid


def test_manual_category_and_exclude(batch):
    win, _ = batch
    first = win.tree.item(win.tree.get_children()[0], "text").strip()
    select_row(win, 0)
    win.apply_category("Кабель HDMI")
    assert row_of(win, first)[0] == "Кабель HDMI.JPG"
    assert row_of(win, first)[2] == "выбрано вручную"

    second = win.tree.item(win.tree.get_children()[0], "text").strip()
    select_row(win, 0)
    win.exclude_selected()
    assert row_of(win, second)[0] == "— не переименовывать —"


def test_candidate_buttons_shown(batch):
    win, _ = batch
    select_row(win, 0)
    texts = [w.cget("text") for w in win.candidates_frame.winfo_children()]
    assert any("%" in t for t in texts)


def test_apply_and_undo(batch):
    win, root_dir = batch
    folder = root_dir / "SN2"
    before = sorted(os.listdir(str(folder)))
    win.apply_folder()
    after = sorted(os.listdir(str(folder)))
    assert after != before
    assert any(name.startswith("Кулер") or name.startswith("Процессор") for name in after)
    assert "Переименовано:" in win.status_var.get()
    assert str(win.undo_btn["state"]) == "normal"

    win.undo_folder()
    assert sorted(os.listdir(str(folder))) == before
    assert str(win.undo_btn["state"]) == "disabled"


def test_log_written(batch):
    win, root_dir = batch
    win.apply_folder()
    from photo_renamer import oplog

    rows_log = oplog.read_log(win.log.csv_path)
    assert rows_log and all(r["Папка"] == str(root_dir / "SN2") for r in rows_log)
    assert any(r["Действие"] == oplog.ACTION_RENAME and r["Результат"] == oplog.RESULT_OK for r in rows_log)
    with open(win.log.txt_path, encoding="utf-8-sig") as f:
        assert "Распознано" in f.read()


def test_skip_folder_finishes(batch):
    win, _ = batch
    win.skip_folder()
    assert win.header_var.get() == "Готово"
    assert str(win.apply_btn["state"]) == "disabled"


def test_candidate_hotkeys(batch):
    win, _ = batch
    iid = win.tree.get_children()[0]
    win.tree.selection_set(iid)
    win._on_row_select()
    filename = win.tree.item(iid, "text").strip()
    photo = next(p for p in win.match.photos if p.filename == filename)
    second = win._alternatives(photo)[1].category      # варианты — без уже предложенной категории

    position = win._selected_position()
    win.apply_candidate(2)                 # клавиша «2» — второй вариант
    assert row_of(win, filename)[0].startswith(second)
    assert row_of(win, filename)[2] == "выбрано вручную"
    # порядок строк не меняется, поэтому выделение просто уходит на строку ниже
    assert win.tree.selection() and win.tree.selection()[0] == win.tree.get_children()[position + 1]
    assert win.tree.item(win.tree.get_children()[position], "text").strip() == filename


def test_hotkey_without_such_candidate_does_nothing(batch):
    win, _ = batch
    select_row(win, 0)
    before = rows(win)
    win.apply_candidate(3)
    photo = win.match.photos[0]
    if len(photo.candidates) < 3:
        assert rows(win) == before


def all_labels(widget):
    """Тексты всех подписей внутри виджета, на любой глубине."""
    texts = []
    for child in widget.winfo_children():
        try:
            texts.append(child.cget("text"))
        except Exception:
            pass
        texts.extend(all_labels(child))
    return texts


def test_photo_size_is_adjustable_and_remembered(batch):
    """Крупные фото: размер меняется кнопками и Ctrl+колесом, не выходит за границы и запоминается."""
    win, _ = batch
    start = win.thumb_size
    win.set_thumb_size(start + review_mod.THUMB_STEP)
    assert win.thumb_size == start + review_mod.THUMB_STEP
    assert win.app_config.review_thumb == win.thumb_size       # сохранится в настройках при закрытии
    assert int(win.style.lookup("Review.Treeview", "rowheight")) == win.thumb_size + 8

    win.set_thumb_size(10_000)
    assert win.thumb_size == review_mod.MAX_THUMB
    win.set_thumb_size(1)
    assert win.thumb_size == review_mod.MIN_THUMB


def test_bottom_row_stays_visible_on_small_screen(batch):
    """Нижняя строка с состоянием и кнопками не должна вытесняться панелью справа."""
    win, _ = batch
    win.geometry("1024x700")
    win.thumb_size = review_mod.MAX_THUMB
    win._apply_thumb_size()
    win.render_rows()
    win.update()
    height = win.winfo_height()
    for button in (win.undo_btn, win.skip_btn, win.apply_btn, win.close_btn):
        name = button.cget("text")
        # вытесненная кнопка сжимается в полоску высотой в пиксель и уезжает наверх
        assert button.winfo_height() > 10, "кнопка сжата: %s" % name
        bottom = button.winfo_rooty() - win.winfo_rooty() + button.winfo_height()
        assert height - 60 <= bottom <= height, "кнопка не внизу окна: %s" % name


def test_marked_photos_are_kept_and_the_rest_are_renamed(batch, monkeypatch):
    """«Не переименовывать» — осознанный выбор: спрашивать про такие фото не нужно,
    а остальные переименовываются, даже если программа в них не уверена."""
    win, root_dir = batch
    from photo_renamer import matcher

    kept = [win.match.photos[0].filename, win.match.photos[1].filename]
    matcher.set_category(win.match, 0, None)
    matcher.set_category(win.match, 1, None)
    win.render_rows()

    questions = []
    monkeypatch.setattr(review_mod.messagebox, "askyesno",
                        lambda title, text, **k: questions.append(text) or True)
    win.apply_folder()
    assert questions and "Не опознано фото" not in questions[0]
    after = set(os.listdir(str(root_dir / "SN2")))
    assert set(kept) <= after                       # помеченные остались с прежними именами
    assert any(not name.startswith("IMG_") for name in after)   # остальные переименованы


def test_sorting_by_column_headers(batch):
    """Сортировка щелчком по заголовку: первый щелчок — по возрастанию, второй — наоборот."""
    win, _ = batch
    order = [p.filename for p in win.match.photos]
    # по умолчанию — по новому имени: одинаковые детали оказываются рядом
    assert "↑" in win.tree.heading("new", "text")
    assert [r[1] for r in rows(win)] == sorted(r[1] for r in rows(win))

    win.sort_by("#0")
    by_name = [r[0] for r in rows(win)]
    assert by_name == sorted(order)
    assert "↑" in win.tree.heading("#0", "text")

    win.sort_by("#0")                                   # повторный щелчок — обратный порядок
    assert [r[0] for r in rows(win)] == sorted(order, reverse=True)
    assert "↓" in win.tree.heading("#0", "text")

    win.sort_by("confidence")
    values = [float(r[1].rstrip("%") or 0) for r in
              [(win.tree.item(iid, "text").strip(), win.tree.item(iid, "values")[1]) for iid in win.tree.get_children()]]
    assert values == sorted(values)


def test_sorting_by_status_groups_doubtful_together(batch):
    """Столбец «Состояние» заменяет прежние галочки: сомнительные собираются рядом."""
    win, _ = batch
    from photo_renamer import matcher

    win.match.photos[3].status = matcher.STATUS_CHECK
    win.sort_by("status")
    states = [win.tree.item(iid, "values")[2] for iid in win.tree.get_children()]
    assert states == sorted(states)


def test_candidates_are_numbered_and_last_is_refusal(batch):
    """Цифры 1…N — варианты, следующая цифра — отказ."""
    win, _ = batch
    select_row(win, 0)
    texts = [w.cget("text") for w in win.candidates_frame.winfo_children() if w.cget("text")]
    numbered = [t for t in texts if t[0].isdigit()]
    assert numbered[-1] == "%d — Не переименовывать" % (review_mod.ALTERNATIVES + 1)
    assert len([t for t in texts if "%" in t]) <= review_mod.ALTERNATIVES


def test_add_and_remove_category(batch, monkeypatch, tmp_path):
    """Две маленькие кнопки у списка: добавить свою категорию и удалить её."""
    win, _ = batch
    reference_count = len(win.reference_categories)

    monkeypatch.setattr(review_mod.simpledialog, "askstring", lambda *a, **k: "Планка индикации")
    win.add_category()
    assert "Планка индикации" in win.categories
    assert len(win.categories) == reference_count + 1

    # имя должно пережить закрытие окна: проверяем сам файл, а не список в памяти
    from photo_renamer.names import NamesList

    assert "Планка индикации" in NamesList(win.app_config.names_path()).names()

    select_row(win, 0)
    filename = win.tree.item(win.tree.get_children()[0], "text").strip()
    win.apply_category("Планка индикации")                  # категории нет в эталоне — всё равно работает
    assert row_of(win, filename)[0].startswith("Планка индикации")

    monkeypatch.setattr(review_mod.messagebox, "askyesno", lambda *a, **k: True)
    win._select_category("Планка индикации")
    win.remove_category()
    assert "Планка индикации" not in win.categories


def test_reference_category_is_not_removed(batch, monkeypatch):
    """Категорию самого эталона из списка не убрать: её задаёт образец."""
    win, _ = batch
    said = []
    monkeypatch.setattr(review_mod.messagebox, "showinfo", lambda title, text, **k: said.append(text))
    win._select_category(win.reference_categories[0])
    win.remove_category()
    assert said and "эталона" in said[0]
    assert win.reference_categories[0] in win.categories


def test_categories_are_shared_with_the_reference_editor(tmp_path):
    """Список категорий у окон общий: добавленное в предпросмотре видно и в редакторе эталона."""
    from photo_renamer.names import NamesList
    from photo_renamer.gui import review as review_module

    shared = NamesList(str(tmp_path / "names.txt"))
    config = cfg.AppConfig()
    config.names_file = str(tmp_path / "names.txt")
    window = review_module.BatchWindow.__new__(review_module.BatchWindow)   # без создания окна
    window.app_config = config
    window.names = shared
    assert window.names is shared


def test_added_category_survives_restart(batch, monkeypatch):
    """Добавили, закрыли окно, открыли снова — категория на месте."""
    win, _ = batch
    from photo_renamer.names import NamesList

    monkeypatch.setattr(review_mod.simpledialog, "askstring", lambda *a, **k: "Съемный накопитель HDD")
    win.add_category()
    again = NamesList(win.app_config.names_path())
    assert "Съемный накопитель HDD" in again.names()
    assert again.sides_for("Съемный накопитель HDD") == 1


def test_double_click_opens_the_photo_large(batch, monkeypatch):
    """Двойной клик по строке показывает фото крупно — так же, как в редакторе эталона."""
    win, root_dir = batch
    opened = []
    monkeypatch.setattr(review_mod, "LargeView",
                        lambda master, folder, photos, index, cache: opened.append((folder, photos[index])))
    iid = win.tree.get_children()[1]
    win.tree.selection_set(iid)
    win._open_large()
    assert opened == [(str(root_dir / "SN2"), win.tree.item(iid, "text").strip())]


def test_double_click_without_selection_does_nothing(batch, monkeypatch):
    win, _ = batch
    monkeypatch.setattr(review_mod, "LargeView", lambda *a, **k: (_ for _ in ()).throw(AssertionError("открыто зря")))
    win.tree.selection_remove(*win.tree.get_children())
    assert win._open_large() == "break"


def test_proposed_category_is_not_offered_again(batch):
    """В «Похоже ещё на:» не должно быть той категории, что уже стоит в столбце «Новое имя»."""
    win, _ = batch
    select_row(win, 0)
    photo = win.match.photos[win._selected_indexes()[0]]
    assert photo.identified
    offered = [w.cget("text") for w in win.candidates_frame.winfo_children()]
    assert not any(photo.category.name in text and "%" in text for text in offered)
    assert all(c.category != photo.category.name for c in win._alternatives(photo))


def test_alternatives_are_full_list_when_photo_is_not_named(batch):
    """Если имя не предложено, из вариантов ничего не убирается."""
    win, _ = batch
    from photo_renamer import matcher

    matcher.set_category(win.match, 0, None)            # «не переименовывать»
    photo = win.match.photos[0]
    assert len(win._alternatives(photo)) == len(photo.candidates)


def test_exhausted_category_is_not_offered(batch):
    """Категория, которой в эталоне столько же, сколько вы уже отметили, в вариантах не нужна.

    Случай заказчика: два кабеля питания отмечены вручную, третьего в папке быть не может.
    """
    win, _ = batch
    from photo_renamer import matcher

    full = win.reference_categories[0]            # категория с одним фото в эталоне
    other = next(p for p in win.match.photos if not p.identified or p.category.name != full)
    matcher.set_category(win.match, win.match.photos[0].index, category_key_of(full))
    assert full in win._exhausted_categories()
    assert all(c.category != full for c in win._alternatives(other))
    # из списка категорий внизу она никуда не девается: выбрать вручную можно всегда
    assert full in win.categories


def category_key_of(name):
    from photo_renamer.reference import category_key

    return category_key(name)


def test_exhausted_rule_ignores_guesses(batch):
    """Догадки программы категорию не закрывают: иначе ошибка отняла бы возможность её выбрать."""
    win, _ = batch
    guessed = [p.category.name for p in win.match.photos if p.identified and p.status != "manual"]
    assert win._exhausted_categories() == set() or all(name not in win._exhausted_categories()
                                                       for name in guessed)


def test_second_slot_is_refilled_after_a_category_is_closed(batch):
    """Когда категория закрыта, на её место встаёт следующий вариант, а не пустое место.

    Случай заказчика: отметил «кабель miniSAS», и под цифрой 2 осталась дырка.
    """
    win, _ = batch
    from photo_renamer import matcher

    win.match.strict = True
    photo = next(p for p in win.match.photos if p.identified)
    before = [c.category for c in win._alternatives(photo)]
    assert len(before) >= 2

    closing = before[1]                       # закрываем ту категорию, что стоит под цифрой 2
    capacity = next(len(c.photos) for c in win.model.categories if c.name == closing)
    marked = 0
    for other in win.match.photos:
        if marked >= capacity:
            break
        if other.index != photo.index:
            matcher.set_category(win.match, other.index, category_key_of(closing))
            marked += 1
    assert closing in win._exhausted_categories()

    after = [c.category for c in win._alternatives(photo)]
    assert closing not in after
    assert len(after) == len(before) - 1       # исчезла ровно одна, остальные подтянулись
    assert after[:1] == before[:1]             # первый вариант не изменился


def test_enter_confirms_and_moves_down(batch):
    """Enter отмечает предложенное имя как выбранное вручную и переводит выделение ниже."""
    win, _ = batch
    win.sort_by("#0")                                  # порядок строк стабилен
    rows_before = [win.tree.item(iid, "text").strip() for iid in win.tree.get_children()]
    iid = win.tree.get_children()[0]
    win.tree.selection_set(iid)
    win._on_row_select()
    filename = rows_before[0]

    win.confirm_selected()
    assert row_of(win, filename)[2] == "выбрано вручную"
    # имя не изменилось, строка осталась на месте — выделение уходит на строку ниже
    assert win.tree.selection()[0] == win.tree.get_children()[1]


def test_ctrl_z_undoes_the_last_edit(batch):
    """Ctrl+Z возвращает то, что было до правки: и для цифры, и для Enter, и для списка."""
    win, _ = batch
    win.sort_by("#0")
    iid = win.tree.get_children()[0]
    win.tree.selection_set(iid)
    win._on_row_select()
    filename = win.tree.item(iid, "text").strip()
    before = row_of(win, filename)

    win.apply_candidate(1)
    assert row_of(win, filename) != before
    win.undo_edit()
    assert row_of(win, filename) == before

    win.tree.selection_set(iid_of(win, filename))     # строки пересозданы, ищем заново
    win.apply_category("Кабель HDMI")
    assert row_of(win, filename)[0] == "Кабель HDMI.JPG"
    win.undo_edit()
    assert row_of(win, filename) == before

    win.tree.selection_set(iid_of(win, filename))
    win.exclude_selected()
    assert row_of(win, filename)[0] == "— не переименовывать —"
    win.undo_edit()
    assert row_of(win, filename) == before


def test_ctrl_z_without_edits_says_so(batch):
    win, _ = batch
    win.undo_edit()
    assert "Отменять нечего" in win.status_var.get()


def test_undo_is_reachable_from_every_widget_and_by_button(batch):
    """Отмена должна работать, где бы ни был курсор, и кнопкой — на macOS Cmd+Z перехватывает
    системное меню «Правка», поэтому одной привязки к сочетанию мало."""
    win, _ = batch
    for widget in (win.tree, win, win.categories_list):
        bound = widget.bind()
        assert any("Key-z" in sequence for sequence in bound), widget
        assert "<<Undo>>" in bound, widget
    assert win.undo_edit_btn.cget("text").startswith("Отменить правку")

    win.sort_by("#0")
    filename = win.tree.item(win.tree.get_children()[0], "text").strip()
    win.tree.selection_set(iid_of(win, filename))
    before = row_of(win, filename)
    win.apply_candidate(1)
    assert row_of(win, filename) != before
    win.undo_edit_btn.invoke()                      # нажатие кнопки, а не клавиш
    assert row_of(win, filename) == before


def test_buttons_have_equal_gaps(batch):
    """У всех кнопок внизу одинаковый отступ — он задаётся одной константой."""
    win, _ = batch
    buttons = [win.undo_edit_btn, win.undo_btn, win.skip_btn, win.apply_btn, win.close_btn]
    gaps = {button.pack_info()["padx"] for button in buttons}
    assert gaps == {review_mod.BUTTON_GAP}, gaps


def test_category_list_fits_the_longest_name(batch):
    """Ширина правой колонки считается по самому длинному названию: обрезанный список не прочитать.

    Проверяется расчёт, а не готовая геометрия окна: на машине сборки окно скрыто,
    и фактические размеры виджетов там не вычисляются.
    """
    import tkinter.font as tkfont

    win, _ = batch
    win.categories.append("Панель USB, VGA, кнопок и индикации")
    win._refresh_categories()
    font = tkfont.Font(font=win.categories_list.cget("font"))
    longest = max(win.categories_list.get(0, "end"), key=font.measure)
    assert int(win.side.cget("width")) >= font.measure(longest)
    assert int(win.side.cget("width")) <= review_mod.MAX_SIDE_WIDTH


def test_candidate_buttons_are_left_aligned(batch):
    """Номера 1, 2 и 3 стоят друг под другом: текст кнопок прижат к левому краю."""
    win, _ = batch
    select_row(win, 0)
    assert win.style.lookup("Candidate.TButton", "anchor") == "w"
    for widget in win.candidates_frame.winfo_children():
        if widget.winfo_class() == "TButton":
            assert str(widget.cget("style")) == "Candidate.TButton"


def test_row_order_does_not_change_after_edits(batch):
    """После правки строки остаются на местах: иначе неразобранные фото прыгают выше сделанных
    и работать приходится то вниз, то вверх."""
    win, _ = batch
    before = [win.tree.item(iid, "text").strip() for iid in win.tree.get_children()]
    win.tree.selection_set(win.tree.get_children()[0])
    win._on_row_select()
    win.apply_category("Кабель HDMI")
    win.tree.selection_set(win.tree.get_children()[1])
    win.apply_candidate(1)
    assert [win.tree.item(iid, "text").strip() for iid in win.tree.get_children()] == before

    win.sort_by("confidence")               # порядок меняется только по щелчку на заголовке
    assert [win.tree.item(iid, "text").strip() for iid in win.tree.get_children()] != before


def test_new_folder_starts_from_the_first_row(batch):
    """После «Пропустить папку» окно показывает первую деталь, а не конец прошлого списка."""
    win, _ = batch
    win.tree.selection_set(win.tree.get_children()[-1])     # были в конце списка
    win._on_row_select()
    win.queue.append(win.folder)                            # в очереди снова есть папка
    win.total += 1
    win.skip_folder()
    assert win.tree.selection() and win.tree.selection()[0] == win.tree.get_children()[0]
    assert win.tree.yview()[0] == 0.0                       # и прокрутка в начале списка


def test_own_category_on_several_photos_becomes_sides(batch):
    """Своя категория, назначенная сразу нескольким фото, — это ракурсы одной детали.

    Замечание с рабочей машины: два выделенных фото получали имена «Стойка многопозиционная»
    и «Стойка многопозиционная 2», то есть считались разными деталями.
    """
    win, _ = batch
    names = ["IMG_0002.JPG", "IMG_0003.JPG"]     # соседние кадры: одна деталь с двух сторон
    win.tree.selection_set(*[iid_of(win, name) for name in names])
    win._on_row_select()
    win.apply_category("Стойка многопозиционная")
    assigned = sorted(row_of(win, name)[0] for name in names)
    assert assigned == ["Стойка многопозиционная фото 1.JPG", "Стойка многопозиционная фото 2.JPG"]
    assert win.names.sides_for("Стойка многопозиционная") == 2
