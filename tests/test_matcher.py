"""Логика сопоставления на заданных вручную расстояниях (без изображений)."""

import numpy as np
import pytest

from photo_renamer.matcher import (
    STATUS_ALREADY_NAMED,
    STATUS_CHECK,
    STATUS_EXCLUDED,
    STATUS_LOW_CONFIDENCE,
    STATUS_MANUAL,
    STATUS_NO_MATCH,
    STATUS_OK,
    STATUS_SIDE_RULE,
    STATUS_UNREADABLE,
    MatchConfig,
    ReferenceModel,
    detect_already_named,
    match_features,
    set_category,
)
from photo_renamer.reference import build_reference, category_key

REF = [
    "Процессор 1 фото 1.jpg",
    "Процессор 1 фото 2.jpg",
    "Оперативная память 1 фото 1.jpg",
    "Оперативная память 1 фото 2.jpg",
    "Оперативная память 2 фото 1.jpg",
    "Оперативная память 2 фото 2.jpg",
    "Кулер.jpg",
    "Кабель HDMI.jpg",
    "Кабель DP.jpg",
    "Кабель DP-miniDP.jpg",
]
FAR = 0.6  # «непохожее» фото: дальше и max_distance, и side_fill_max_distance


class FakeModel(ReferenceModel):
    """Модель эталона, у которой расстояния заданы таблицей: фото → {категория: расстояние}."""

    def __init__(self, rows, side_rows=None, ref_names=REF):
        reference = build_reference("/ref", ref_names)
        super().__init__(reference, extractor=None, ref_features=[object()] * len(ref_names))
        n_cat = len(self.categories)
        self._cat = np.full((len(rows), n_cat), FAR)
        self._side = np.full((len(rows), n_cat, self.max_sides), np.inf)
        for i, row in enumerate(rows):
            for name, d in row.items():
                c = self.cat_index[category_key(name)]
                self._cat[i, c] = d
            for c in range(n_cat):
                self._side[i, c, : self.categories[c].sides] = self._cat[i, c]
        for (i, name, side), d in (side_rows or {}).items():
            self._side[i, self.cat_index[category_key(name)], side - 1] = d

    def distances(self, target_features, weights, adapt_power=0.0):
        idx = list(target_features)  # «признаки» — это индексы фото
        return self._cat[idx], self._side[idx], dict(weights)


def run(rows, names=None, side_rows=None, config=None, unreadable=(), ref_names=REF):
    """По умолчанию — обычный режим (строгий проверяется отдельно) и без «имя каждому фото»:
    большинство тестов проверяют именно раскладку, а не поведение при низкой уверенности."""
    config = config or MatchConfig(strict_counts=False, always_propose=False)
    names = names or ["DSC_%04d.JPG" % (i + 1) for i in range(len(rows))]
    model = FakeModel(rows, side_rows, ref_names)
    feats = [None if i in unreadable else i for i in range(len(rows))]
    fixed = {i: detect_already_named(model.reference, n) for i, n in enumerate(names)}
    fixed = {i: v for i, v in fixed.items() if v is not None}
    for i in fixed:
        feats[i] = None
    return match_features(model, "/target", names, feats, config, fixed=fixed,
                          unreadable={i: "битый файл" for i in unreadable})


def new_names(result):
    return [p.new_name for p in result.photos]


def test_full_folder_in_shuffled_order():
    rows = [
        {"Кулер": 0.08},
        {"Оперативная память": 0.10},
        {"Оперативная память": 0.12},
        {"Процессор": 0.09},
        {"Процессор": 0.11},
        {"Кабель HDMI": 0.07},
        {"Оперативная память": 0.10},
        {"Оперативная память": 0.13},
        {"Кабель DP": 0.10},
        {"Кабель DP-miniDP": 0.10},
    ]
    result = run(rows)
    assert new_names(result) == [
        "Кулер.JPG",
        "Оперативная память 1 фото 1.JPG",
        "Оперативная память 1 фото 2.JPG",
        "Процессор 1 фото 1.JPG",
        "Процессор 1 фото 2.JPG",
        "Кабель HDMI.JPG",
        "Оперативная память 2 фото 1.JPG",
        "Оперативная память 2 фото 2.JPG",
        "Кабель DP.JPG",
        "Кабель DP-miniDP.JPG",
    ]
    assert all(p.status == STATUS_OK for p in result.photos)
    assert all(p.confidence > 90 for p in result.photos)
    assert result.warnings == []


def test_hungarian_separates_similar_cables():
    # Оба кабеля ближе к «Кабель DP». Жадный выбор дал бы два DP, оптимальный — по одному
    rows = [
        {"Кабель DP": 0.10, "Кабель DP-miniDP": 0.12},
        {"Кабель DP": 0.11, "Кабель DP-miniDP": 0.20},
    ]
    result = run(rows)
    assert [p.category.name for p in result.photos] == ["Кабель DP-miniDP", "Кабель DP"]
    # уверенность учитывает, что второй «Кабель DP» был бы лишним относительно эталона
    assert result.photos[0].status == STATUS_OK
    assert result.photos[0].confidence >= 60


def test_extra_instance_is_identified_and_numbered():
    rows = [{"Кулер": 0.08}, {"Кабель HDMI": 0.07}, {"Кулер": 0.09}]
    result = run(rows)
    assert new_names(result) == ["Кулер.JPG", "Кабель HDMI.JPG", "Кулер 2.JPG"]
    assert result.category_counts() == {"Кулер": 2, "Кабель HDMI": 1}


def test_unrelated_photo_is_not_matched():
    result = run([{"Кулер": 0.08}, {}])
    unknown = result.photos[1]
    assert unknown.status == STATUS_NO_MATCH
    assert unknown.new_name is None
    assert len(unknown.candidates) == MatchConfig().top_n     # кандидаты запоминаются с запасом
    assert result.unidentified() == [unknown]


def test_ambiguous_photo_is_low_confidence_with_candidates():
    # равные расстояния до двух категорий, у обеих есть свободные места
    result = run([{"Кабель DP": 0.10, "Кабель DP-miniDP": 0.10}])
    photo = result.photos[0]
    assert photo.status == STATUS_LOW_CONFIDENCE
    assert photo.new_name is None
    assert {c.category for c in photo.candidates[:2]} == {"Кабель DP", "Кабель DP-miniDP"}
    assert photo.candidates[0].confidence == pytest.approx(photo.candidates[1].confidence)


def test_confidence_threshold_is_configurable():
    rows = [{"Кабель DP": 0.10, "Кабель DP-miniDP": 0.10}]
    assert run(rows, config=MatchConfig(always_propose=False, min_confidence=40)).photos[0].status == STATUS_OK


def test_sequence_prior_rescues_weak_side():
    """Учёт порядка съёмки: слабо похожая вторая сторона опознаётся благодаря соседу."""
    rows = [{"Оперативная память": 0.10}, {"Оперативная память": 0.45}, {"Кулер": 0.08}]
    result = run(rows)
    assert result.photos[1].status == STATUS_OK
    assert new_names(result)[1] == "Оперативная память 1 фото 2.JPG"


def test_side_rule_completes_pair():
    # без учёта соседей: вторая сторона дальше max_distance 0.40, но ближе side_fill 0.50
    rows = [{"Оперативная память": 0.10}, {"Оперативная память": 0.45}, {"Кулер": 0.08}]
    result = run(rows, config=MatchConfig(always_propose=False, strict_counts=False, sequence_weight=0))
    assert result.photos[1].status == STATUS_SIDE_RULE
    assert new_names(result) == [
        "Оперативная память 1 фото 1.JPG",
        "Оперативная память 1 фото 2.JPG",
        "Кулер.JPG",
    ]
    assert result.warnings == []


def test_side_rule_does_not_take_dissimilar_or_identified_neighbors():
    rows = [{"Оперативная память": 0.10}, {"Кулер": 0.08}, {"Процессор": 0.10}, {}]
    result = run(rows, config=MatchConfig(always_propose=False, strict_counts=False, sequence_weight=0))
    assert result.photos[1].category.name == "Кулер"
    assert result.photos[3].status == STATUS_NO_MATCH  # FAR > side_fill_max_distance
    assert any("Неполный комплект сторон: Оперативная память 1" in w for w in result.warnings)
    assert any("Неполный комплект сторон: Процессор 1" in w for w in result.warnings)


def test_run_of_four_splits_into_two_instances():
    rows = [{"Оперативная память": 0.1}] * 4
    assert new_names(run(rows)) == [
        "Оперативная память 1 фото 1.JPG",
        "Оперативная память 1 фото 2.JPG",
        "Оперативная память 2 фото 1.JPG",
        "Оперативная память 2 фото 2.JPG",
    ]


def test_incomplete_run_alignment_uses_side_distances():
    """Фото получает ту сторону, на которую похоже, а не ту, что выпала по счёту.

    Иначе один лишний или недостающий кадр сдвигал бы стороны у всех следующих деталей.
    """
    rows = [{"Оперативная память": 0.1}] * 3
    side_rows = {
        (0, "Оперативная память", 1): 0.30, (0, "Оперативная память", 2): 0.10,
        (1, "Оперативная память", 1): 0.10, (1, "Оперативная память", 2): 0.30,
        (2, "Оперативная память", 1): 0.30, (2, "Оперативная память", 2): 0.10,
    }
    result = run(rows, side_rows=side_rows)
    # первые два фото — один экземпляр: стороны 2 и 1. Порядок сторон внутри экземпляра любой,
    # на реальных съёмках деталь снимают то с одной стороны, то с другой
    assert [(p.instance, p.side) for p in result.photos] == [(1, 2), (1, 1), (2, 2)]
    assert new_names(result)[0] == "Оперативная память 1 фото 2.JPG"
    assert any("Оперативная память 2 — 1 фото из 2" in w for w in result.warnings)


def test_single_side_consecutive_are_separate_instances():
    rows = [{"Кабель HDMI": 0.07}, {"Кабель HDMI": 0.08}]
    assert new_names(run(rows)) == ["Кабель HDMI.JPG", "Кабель HDMI 2.JPG"]


def test_three_sided_category():
    ref = ["Видеокарта 1 фото 1.jpg", "Видеокарта 1 фото 2.jpg", "Видеокарта 1 фото 3.jpg", "Кулер.jpg"]
    rows = [{"Видеокарта": 0.1}] * 3 + [{"Кулер": 0.1}]
    assert new_names(run(rows, ref_names=ref)) == [
        "Видеокарта 1 фото 1.JPG", "Видеокарта 1 фото 2.JPG", "Видеокарта 1 фото 3.JPG", "Кулер.JPG",
    ]


def test_already_named_photos_are_kept_and_numbers_skipped():
    names = [
        "Оперативная память 1 фото 1.jpg",
        "Оперативная память 1 фото 2.jpg",
        "DSC_0003.jpg",
        "DSC_0004.jpg",
        "Кулер.jpg",
    ]
    rows = [{}, {}, {"Оперативная память": 0.1}, {"Оперативная память": 0.1}, {}]
    result = run(rows, names=names)
    assert [p.status for p in result.photos][:2] == [STATUS_ALREADY_NAMED] * 2
    assert result.photos[4].status == STATUS_ALREADY_NAMED
    assert new_names(result) == [None, None, "Оперативная память 2 фото 1.jpg", "Оперативная память 2 фото 2.jpg", None]


def test_detect_already_named_is_strict():
    ref = build_reference("/ref", REF)
    assert detect_already_named(ref, "Процессор 1 фото 2.JPG") is not None
    assert detect_already_named(ref, "Процессор 1 фото 3.jpg") is None      # нет такой стороны
    assert detect_already_named(ref, "Процессор  1 фото 1.jpg") is None     # не каноническая запись
    assert detect_already_named(ref, "Неизвестно 1.jpg") is None
    assert detect_already_named(ref, "DSC_0001.JPG") is None


def test_unreadable_photo():
    result = run([{"Кулер": 0.08}, {"Кабель HDMI": 0.07}], unreadable={0})
    assert result.photos[0].status == STATUS_UNREADABLE
    assert "битый файл" in result.photos[0].note
    assert new_names(result) == [None, "Кабель HDMI.JPG"]


def test_manual_correction_renumbers():
    rows = [{"Кабель HDMI": 0.07}, {}, {"Кабель HDMI": 0.08}]
    result = run(rows)
    assert new_names(result) == ["Кабель HDMI.JPG", None, "Кабель HDMI 2.JPG"]

    set_category(result, 1, category_key("Кабель HDMI"))
    assert result.photos[1].status == STATUS_MANUAL
    assert new_names(result) == ["Кабель HDMI.JPG", "Кабель HDMI 2.JPG", "Кабель HDMI 3.JPG"]

    set_category(result, 0, None)
    assert result.photos[0].status == STATUS_EXCLUDED
    assert new_names(result) == [None, "Кабель HDMI.JPG", "Кабель HDMI 2.JPG"]


def test_manual_pairing_of_sides():
    rows = [{"Процессор": 0.1}, {}]
    result = run(rows)
    set_category(result, 1, category_key("Процессор"))
    assert new_names(result) == ["Процессор 1 фото 1.JPG", "Процессор 1 фото 2.JPG"]
    assert result.warnings == []


def test_empty_folder_and_all_fixed():
    assert run([]).photos == []
    result = run([{}], names=["Кулер.jpg"])
    assert result.photos[0].status == STATUS_ALREADY_NAMED


def test_strict_counts_when_folder_matches_reference():
    """Состав совпадает с эталоном: мест ровно столько, сколько фото в эталоне, имя даётся каждому фото."""
    rows = [
        {"Процессор": 0.10}, {"Процессор": 0.12},
        {"Оперативная память": 0.10}, {"Оперативная память": 0.11},
        {"Оперативная память": 0.12}, {"Оперативная память": 0.13},
        {"Кулер": 0.10}, {"Кабель HDMI": 0.10},
        {"Кабель DP": 0.20, "Кабель DP-miniDP": 0.22},   # оба кабеля похожи на DP
        {"Кабель DP": 0.21, "Кабель DP-miniDP": 0.45},
    ]
    result = run(rows, config=MatchConfig(strict_counts="auto"))
    assert result.strict
    assert all(p.identified for p in result.photos)
    # каждой категории досталось ровно столько фото, сколько в эталоне
    assert result.category_counts() == {"Процессор": 2, "Оперативная память": 4, "Кулер": 1,
                                        "Кабель HDMI": 1, "Кабель DP": 1, "Кабель DP-miniDP": 1}
    assert [p.category.name for p in result.photos[8:]] == ["Кабель DP-miniDP", "Кабель DP"]


def test_strict_counts_off_when_folder_differs():
    rows = [{"Кулер": 0.10}, {"Кабель HDMI": 0.10}]      # фото меньше, чем в эталоне
    result = run(rows, config=MatchConfig(strict_counts="auto"))
    assert not result.strict


def test_strict_counts_marks_doubtful_photos():
    rows = [
        {"Процессор": 0.10}, {"Процессор": 0.12},
        {"Оперативная память": 0.10}, {"Оперативная память": 0.11},
        {"Оперативная память": 0.12}, {"Оперативная память": 0.13},
        {"Кулер": 0.10}, {"Кабель HDMI": 0.10},
        {"Кабель DP": 0.30, "Кабель DP-miniDP": 0.30},   # неоднозначно
        {"Кабель DP": 0.30, "Кабель DP-miniDP": 0.30},
    ]
    result = run(rows, config=MatchConfig(strict_counts=True, min_confidence=90))
    doubtful = [p for p in result.photos if p.status == STATUS_CHECK]
    assert doubtful and all("проверьте" in p.note for p in doubtful)
    assert all(p.new_name for p in result.photos)        # имя предложено всем


def test_manual_choice_propagates_to_other_photos():
    """Исправление пользователя пересчитывает раскладку: место категории занято, и соседи встают верно."""
    rows = [
        {"Кабель DP": 0.10, "Кабель DP-miniDP": 0.12},
        {"Кабель DP": 0.11, "Кабель DP-miniDP": 0.13},
    ]
    result = run(rows)
    assert [p.category.name for p in result.photos] == ["Кабель DP-miniDP", "Кабель DP"]

    # пользователь говорит: первое фото — это «Кабель DP»
    set_category(result, 0, category_key("Кабель DP"))
    assert [p.category.name for p in result.photos] == ["Кабель DP", "Кабель DP-miniDP"]
    assert result.photos[0].status == STATUS_MANUAL

    from photo_renamer.matcher import clear_category

    clear_category(result, 0)
    assert [p.category.name for p in result.photos] == ["Кабель DP-miniDP", "Кабель DP"]


def test_manual_choice_frees_capacity_for_others():
    rows = [{"Кулер": 0.10}, {"Кулер": 0.12}, {"Кабель HDMI": 0.30, "Кулер": 0.14}]
    result = run(rows)
    assert result.category_counts()["Кулер"] >= 1
    set_category(result, 0, None)                       # первое фото не переименовывать
    assert result.photos[0].status == STATUS_EXCLUDED
    assert result.photos[0].new_name is None
    assert result.photos[1].category.name == "Кулер"    # нумерация и раскладка пересчитаны
    assert result.photos[1].new_name == "Кулер.JPG"


def test_runs_split_by_sides_only():
    """Деление серии на экземпляры идёт только по числу сторон: паузы между кадрами не используются."""
    rows = [{"Оперативная память": 0.10}] * 4
    assert [(p.instance, p.side) for p in run(rows).photos] == [(1, 1), (1, 2), (2, 1), (2, 2)]


def test_new_photos_renumber_existing_by_shooting_order():
    """Часть трансиверов уже названа, часть опознана сейчас: номера должны идти по порядку съёмки,
    иначе нельзя понять, какой ракурс к какой детали."""
    names = ["DSC_0001.JPG", "DSC_0002.JPG", "Оперативная память 1 фото 1.JPG", "Оперативная память 1 фото 2.JPG"]
    rows = [{"Оперативная память": 0.10}, {"Оперативная память": 0.11}, {}, {}]
    result = run(rows, names=names)
    # новые фото сняты раньше, поэтому они — экземпляр 1, а уже названные становятся вторым
    assert new_names(result) == [
        "Оперативная память 1 фото 1.JPG",
        "Оперативная память 1 фото 2.JPG",
        "Оперативная память 2 фото 1.JPG",
        "Оперативная память 2 фото 2.JPG",
    ]
    assert "перенумерация" in result.photos[2].note


def test_existing_names_untouched_without_new_photos_of_that_category():
    names = ["Оперативная память 1 фото 1.JPG", "Оперативная память 1 фото 2.JPG", "DSC_0003.JPG"]
    rows = [{}, {}, {"Кулер": 0.10}]
    result = run(rows, names=names)
    assert new_names(result) == [None, None, "Кулер.JPG"]   # чужую категорию не трогаем


def test_renumbering_can_be_switched_off():
    names = ["DSC_0001.JPG", "DSC_0002.JPG", "Оперативная память 1 фото 1.JPG", "Оперативная память 1 фото 2.JPG"]
    rows = [{"Оперативная память": 0.10}, {"Оперативная память": 0.11}, {}, {}]
    result = run(rows, names=names, config=MatchConfig(always_propose=False, strict_counts=False, renumber_existing=False))
    assert new_names(result)[:2] == ["Оперативная память 2 фото 1.JPG", "Оперативная память 2 фото 2.JPG"]
    assert new_names(result)[2:] == [None, None]


def test_block_rule_fixes_pair_inside_another_series():
    """Пара фото, ошибочно отнесённая к чужой категории внутри серии, исправляется правилом серий.

    Так выглядит реальная ошибка на фотографиях: посреди серии «оперативная память» два кадра
    оказались чуть ближе к процессору. Соседи голосуют за память, и пара возвращается в серию.
    """
    rows = [{"Оперативная память": 0.10}] * 3 \
        + [{"Оперативная память": 0.125, "Процессор": 0.12}] * 2 \
        + [{"Оперативная память": 0.10}] * 3
    # лишние экземпляры и соседние расстояния здесь не при чём: проверяется только правило серий
    base = dict(strict_counts=False, sequence_weight=0.0, extra_penalty=0.0)
    without = run(rows, config=MatchConfig(block_weight=0.0, **base))
    with_rule = run(rows, config=MatchConfig(**base))
    assert [p.category.name for p in without.photos][3:5] == ["Процессор", "Процессор"]
    assert [p.category.name for p in with_rule.photos] == ["Оперативная память"] * 8


def test_block_rule_keeps_single_photo_categories():
    """Деталь, которой в эталоне одно фото, серии иметь не может, и правило её не наказывает."""
    rows = [{"Оперативная память": 0.10}] * 2 + [{"Кулер": 0.10}] + [{"Оперативная память": 0.10}] * 2
    result = run(rows)
    assert [p.category.name for p in result.photos] == [
        "Оперативная память", "Оперативная память", "Кулер", "Оперативная память", "Оперативная память"]


def test_block_penalty_without_neighbours_is_zero():
    """Если рядом нет ни одного распознанного фото, подсказки нет и штрафа тоже нет."""
    from photo_renamer.matcher import block_penalty

    penalty = block_penalty([0, 1, 2], [-1, -1, -1], np.array([4, 4]), weight=0.1, span=3)
    assert penalty.shape == (3, 2)
    assert not penalty.any()


THREE_SIDES = ["Съемный накопитель 1 фото 1.jpg", "Съемный накопитель 1 фото 2.jpg",
               "Съемный накопитель 1 фото 3.jpg", "Кулер.jpg"]


def _sides_by_look(order):
    """Расстояния до сторон: фото k явно похоже на сторону order[k], до остальных — далеко."""
    rows = [{"Съемный накопитель": 0.10} for _ in order]
    side_rows = {}
    for k, side in enumerate(order):
        for s in (1, 2, 3):
            side_rows[(k, "Съемный накопитель", s)] = 0.05 if s == side else 0.35
    return rows, side_rows


def test_sides_are_named_by_look_not_by_count():
    """Деталь снимают в произвольном порядке сторон: сначала корпус, потом две стороны платы.

    Случай заказчика: «фото 3» (корпус) не должно становиться «фото 1» следующего изделия.
    """
    rows, side_rows = _sides_by_look([3, 1, 2, 3, 1, 2])
    result = run(rows, side_rows=side_rows, ref_names=THREE_SIDES)
    assert new_names(result) == [
        "Съемный накопитель 1 фото 3.JPG",
        "Съемный накопитель 1 фото 1.JPG",
        "Съемный накопитель 1 фото 2.JPG",
        "Съемный накопитель 2 фото 3.JPG",
        "Съемный накопитель 2 фото 1.JPG",
        "Съемный накопитель 2 фото 2.JPG",
    ]
    assert result.warnings == []


def test_missing_side_does_not_shift_the_following_items():
    """У второго изделия нет фото одной стороны: сдвинуться должно только оно само."""
    rows, side_rows = _sides_by_look([3, 1, 2, 3, 2, 3, 1, 2])   # у второго нет стороны 1
    result = run(rows, side_rows=side_rows, ref_names=THREE_SIDES)
    assert [(p.instance, p.side) for p in result.photos] == [
        (1, 3), (1, 1), (1, 2),
        (2, 3), (2, 2),
        (3, 3), (3, 1), (3, 2),
    ]
    assert any("Съемный накопитель 2 — 2 фото из 3" in w for w in result.warnings)


def test_sides_shot_in_separate_blocks_are_joined():
    """Фотограф снял сначала обе стороны платы у всех изделий, а корпуса — отдельным блоком.

    Случай заказчика: 22 фото плат подряд (11 изделий), потом другие детали, потом 11 корпусов.
    Первый корпус относится к первому изделию, второй ко второму и так далее.
    """
    order = [1, 2] * 3 + [0] + [3] * 3          # 0 — посторонний снимок между блоками
    rows, side_rows = _sides_by_look([s if s else 1 for s in order])
    for k, side in enumerate(order):
        if side == 0:                            # это фото не относится к категории
            rows[k] = {}
            side_rows = {key: value for key, value in side_rows.items() if key[0] != k}
    result = run(rows, side_rows=side_rows, ref_names=THREE_SIDES)
    assert [(p.instance, p.side) for p in result.photos] == [
        (1, 1), (1, 2), (2, 1), (2, 2), (3, 1), (3, 2),
        (None, None),
        (1, 3), (2, 3), (3, 3),
    ]
    assert result.warnings == []


def test_separate_blocks_with_same_sides_are_different_items():
    """Два блока с одинаковыми сторонами — это разные детали, склеивать их нельзя."""
    rows, side_rows = _sides_by_look([1, 2, 1, 2])
    rows[2] = {"Съемный накопитель": 0.10}
    result = run(rows, side_rows=side_rows, ref_names=THREE_SIDES)
    assert [(p.instance, p.side) for p in result.photos] == [(1, 1), (1, 2), (2, 1), (2, 2)]
