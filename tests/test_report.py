from photo_renamer import oplog
from photo_renamer.renamer import RenameItem, RenamePlan
from photo_renamer.report import build_report, format_report
from test_matcher import run as run_match

ALL = [
    {"Процессор": 0.1}, {"Процессор": 0.1},
    {"Оперативная память": 0.1}, {"Оперативная память": 0.1},
    {"Оперативная память": 0.1}, {"Оперативная память": 0.1},
    {"Кулер": 0.1}, {"Кабель HDMI": 0.1}, {"Кабель DP": 0.1}, {"Кабель DP-miniDP": 0.1},
]


def test_folder_matching_reference():
    report = build_report(run_match(ALL), excluded=1)
    assert report.percent == 100 and report.matches_reference
    text = format_report(report)
    assert "Состав совпадает с эталоном" in text
    assert "рентгенограмм пропущено: 1" in text


def test_missing_extra_unidentified_and_problem_categories():
    rows = [
        {"Процессор": 0.1}, {"Процессор": 0.1},
        {"Оперативная память": 0.1}, {"Оперативная память": 0.1},
        {"Кулер": 0.1}, {"Кулер": 0.1},                       # лишний кулер
        {"Кабель DP": 0.10, "Кабель DP-miniDP": 0.10},      # неоднозначный кабель
        {},                                                 # посторонний снимок
    ]
    match = run_match(rows)
    report = build_report(match, problem_categories=["кабель dp", "Кабель DP-miniDP", "Кабель HDMI"])
    assert report.total == 8 and report.identified == 6
    assert round(report.percent) == 75
    assert [(d.name, d.expected, d.actual) for d in report.missing] == [
        ("Оперативная память", 4, 2), ("Кабель HDMI", 1, 0), ("Кабель DP", 1, 0), ("Кабель DP-miniDP", 1, 0)]
    assert [(d.name, d.expected, d.actual) for d in report.extra] == [("Кулер", 1, 2)]
    assert [p.filename for p in report.unidentified] == ["DSC_0007.JPG", "DSC_0008.JPG"]
    assert report.problem_categories == ["Кабель HDMI", "Кабель DP", "Кабель DP-miniDP"]

    text = format_report(report)
    assert "не хватает: Оперативная память — 2 из 4" in text
    assert "лишние:     Кулер — 2 при 1 в эталоне" in text
    assert "DSC_0007.JPG — низкая уверенность" in text and "кандидаты: Кабель DP" in text
    assert "НЕ УДАЛОСЬ РАСПОЗНАТЬ АВТОМАТИЧЕСКИ: Кабель HDMI, Кабель DP, Кабель DP-miniDP" in text


def test_plan_and_result_sections():
    report = build_report(run_match([{"Кулер": 0.1}]))
    plan = RenamePlan("/x", [RenameItem("DSC_0001.JPG", "Кулер.JPG", "Кулер", 97.0)])
    assert "DSC_0001.JPG → Кулер.JPG  (97%)" in format_report(report, plan)
    plan.items[0].result = oplog.RESULT_OK
    assert "переименовано 1, пропущено 0, ошибок 0" in format_report(report, plan, applied=True)


def test_check_section_in_strict_mode():
    from photo_renamer.matcher import MatchConfig

    rows = [
        {"Процессор": 0.10}, {"Процессор": 0.12},
        {"Оперативная память": 0.10}, {"Оперативная память": 0.11},
        {"Оперативная память": 0.12}, {"Оперативная память": 0.13},
        {"Кулер": 0.10}, {"Кабель HDMI": 0.10},
        {"Кабель DP": 0.30, "Кабель DP-miniDP": 0.30},
        {"Кабель DP": 0.30, "Кабель DP-miniDP": 0.30},
    ]
    match = run_match(rows, config=MatchConfig(strict_counts=True, min_confidence=90))
    report = build_report(match)
    assert report.to_check and report.identified == 10
    text = format_report(report)
    assert "Проверьте имена (низкая уверенность)" in text
