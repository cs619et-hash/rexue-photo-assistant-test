import tkinter as tk
import os
from datetime import datetime
from pathlib import Path

from app import App, Match


def test_gui_opens_and_checkboxes_respond():
    app = App()
    app.withdraw()
    app.update_idletasks()
    assert app.event_var.get() == ""
    app.zhixian_var.set(True)
    app.zhicheng_var.set(False)
    app.photographer_changed()
    assert "植先" in app.status.get()
    app.zhixian_var.set(False)
    app.zhicheng_var.set(True)
    app.photographer_changed()
    assert "植丞" in app.status.get()
    app.destroy()


def test_csv_without_colours_uses_only_checked_photographer():
    app = App()
    app.withdraw()
    app.matches = [
        Match(datetime(2026, 9, 12), datetime(2026, 9, 12, 8), "甲", "U10", "大將 vs 安東國小"),
    ]
    app.zhixian_var.set(False)
    app.zhicheng_var.set(True)
    app.render_matches()
    items = app.tree.get_children()
    assert len(items) == 1
    assert app.tree.item(items[0], "values")[0] == "植丞"
    assert "目前顯示 1 場" in app.status.get()
    app.destroy()


def test_no_photographer_checked_hides_rows_without_false_selection():
    app = App()
    app.withdraw()
    app.matches = [
        Match(datetime(2026, 9, 12), datetime(2026, 9, 12, 8), "甲", "U10", "大將 vs 安東國小"),
    ]
    app.zhixian_var.set(False)
    app.zhicheng_var.set(False)
    app.render_matches()
    assert app.tree.get_children() == ()
    assert app.selected_names() == set()
    app.destroy()


def test_google_sheet_is_kept_inside_app_without_showing_file_path():
    app = App()
    app.withdraw()
    rows = [
        ["比賽日期", "比賽開始時間", "比賽地點", "年級組別", "隊伍名稱/球員姓名/背號", "比賽隊伍名稱"],
        ["2026年09月12日", "0800", "甲", "U10", "大將", "安東國小"],
    ]
    app._finish_google_load(rows, {})
    assert app.source_var.get() == "Google 預約表（已自動載入）"
    assert app.google_rows == rows
    assert len(app.matches) == 1
    app.matches = []
    app.load_schedule()
    assert len(app.matches) == 1
    app.destroy()


def test_browser_download_message_does_not_claim_login_is_required(monkeypatch, tmp_path):
    app = App()
    app.withdraw()
    monkeypatch.setattr("app.webbrowser.open", lambda url: True)
    monkeypatch.setattr("app.threading.Thread.start", lambda self: None)
    monkeypatch.setattr("app.Path.home", lambda: tmp_path)
    app._open_google_in_browser("https://docs.google.com/spreadsheets/d/abc123/edit")
    assert app.status.get() == "已開啟瀏覽器下載預約表，下載完成後會自動帶入程式，請勿關閉程式。"
    app.destroy()


def test_csv_flow_creates_folder_and_sorts_photo(tmp_path: Path):
    app = App()
    app.withdraw()
    rows = [
        ["比賽日期", "比賽開始時間", "比賽地點", "年級組別", "隊伍名稱/球員姓名/背號", "比賽隊伍名稱"],
        ["2026年09月12日", "0800", "甲", "U10", "大將", "安東國小"],
    ]
    source = tmp_path / "照片"
    output = tmp_path / "輸出"
    source.mkdir()
    photo = source / "DSC0001.JPG"
    photo.write_bytes(b"test-photo")
    timestamp = datetime(2026, 9, 12, 8, 20).timestamp()
    os.utime(photo, (timestamp, timestamp))

    app.zhixian_var.set(False)
    app.zhicheng_var.set(True)
    app.event_var.set("桃園獵鷹盃")
    app.photo_var.set(str(source))
    app.output_var.set(str(output))
    app.show_rows(rows, {})
    app.create_folders()
    folder = output / "2026.09.12-桃園獵鷹盃-U10 大將 vs 安東國小"
    assert folder.is_dir()
    sorted_count, review_count = app.sort_selected(source, output, "桃園獵鷹盃")
    assert (sorted_count, review_count) == (1, 0)
    assert (folder / "DSC0001.JPG").read_bytes() == b"test-photo"
    app.destroy()
