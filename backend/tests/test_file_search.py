"""app.domains.file_search against a temporary folder tree."""

import asyncio
import os
import time

from app.domains import file_search
from app.tools.registry import TurnContext


def _tree(tmp_path):
    docs = tmp_path / "Documents"
    (docs / "работа").mkdir(parents=True)
    (docs / "node_modules").mkdir()
    old = docs / "Отчеты_сентябрь_2025.docx"
    new = docs / "работа" / "Отчёт за сентябрь 2026.pdf"
    for f in (old, new, docs / "Отпуск сентябрь.jpg", docs / "node_modules" / "отчет сентябрь.js",
              docs / "run_отчет_сентябрь.bat"):
        f.write_text("x", encoding="utf-8")
    os.utime(old, (time.time() - 86400, time.time() - 86400))
    return docs


def test_word_stems_match_and_newest_come_first(tmp_path):
    docs = _tree(tmp_path)
    found = file_search.search("отчёт за сентябрь", [docs])
    names = [os.path.basename(f["path"]) for f in found]
    # both words match in all three; the "Отпуск" photo has one - the full matches win;
    # the one in node_modules is skipped
    assert set(names) == {"Отчёт за сентябрь 2026.pdf", "run_отчет_сентябрь.bat", "Отчеты_сентябрь_2025.docx"}
    assert names[-1] == "Отчеты_сентябрь_2025.docx"  # a day older - last


def test_open_the_best_match_but_never_a_program(tmp_path):
    docs = _tree(tmp_path)
    opened = []
    handler = file_search.make_handler(roots=lambda: [docs], opener=opened.append)
    result = asyncio.run(handler({"name": "отчёт сентябрь 2026", "open": True}, TurnContext()))
    assert opened == [result["opened"]] and result["opened"].endswith(".pdf")
    result = asyncio.run(handler({"name": "run отчет", "open": True}, TurnContext()))
    assert "program or script" in result["error"] and len(opened) == 1


def test_nothing_found_and_a_named_folder(tmp_path):
    docs = _tree(tmp_path)
    handler = file_search.make_handler(roots=lambda: [docs])
    assert asyncio.run(handler({"name": "диплом"}, TurnContext()))["found"] == []
    assert "No folder" in asyncio.run(handler({"name": "отчет", "folder": str(tmp_path / "нет")}, TurnContext()))["error"]
    assert asyncio.run(handler({"name": "отчет", "folder": str(docs / "работа")}, TurnContext()))["found"]
