from __future__ import annotations

import csv
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


APP_NAME = "熱血少年｜賽事照片整理助手"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".arw", ".png", ".heic", ".tif", ".tiff"}
INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class Match:
    date: datetime
    start: datetime
    venue: str
    group: str
    matchup: str
    request: str = ""
    photographer: str = "待確認"

    def folder_name(self, event_name: str) -> str:
        date_text = self.date.strftime("%Y.%m.%d")
        return sanitize(f"{date_text}-{event_name}{self.group}{normalize_matchup(self.matchup)}")


def sanitize(value: str) -> str:
    value = INVALID.sub("_", value).strip().rstrip(".")
    return re.sub(r"\s+", " ", value)


def normalize_matchup(value: str) -> str:
    value = value.strip()
    value = re.sub(r"\s*v\s*\.?\s*s\.?\s*", " vs ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*(?<![A-Za-z])v(?![A-Za-z])\s*", " vs ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*[/／]\s*", " vs ", value)
    return re.sub(r"\s+", " ", value)


def has_match_separator(value: str) -> bool:
    return bool(re.search(r"v\s*\.?\s*s\.?|(?<![A-Za-z])v(?![A-Za-z])|[/／]", value, re.IGNORECASE))


def own_team(value: str) -> str:
    return re.split(r"[/／]", value.strip(), maxsplit=1)[0].strip()


def parse_date(value: str) -> datetime:
    m = re.search(r"(20\d{2})\D+(\d{1,2})\D+(\d{1,2})", value)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    try:
        serial = float(value)
        if 30000 <= serial <= 80000:
            return datetime(1899, 12, 30) + timedelta(days=serial)
    except ValueError:
        pass
    raise ValueError(f"無法辨識日期：{value}")


def parse_start(day: datetime, value: str) -> datetime:
    digits = re.sub(r"\D", "", str(value))
    if len(digits) <= 2:
        hour, minute = int(digits), 0
    elif len(digits) == 3:
        hour, minute = int(digits[0]), int(digits[1:])
    else:
        hour, minute = int(digits[:-2]), int(digits[-2:])
    if hour > 23 or minute > 59:
        raise ValueError(f"無法辨識時間：{value}")
    return day.replace(hour=hour, minute=minute)


def sheet_export_url(url: str, fmt: str = "xlsx") -> str:
    m = re.search(r"/spreadsheets/d/([\w-]+)", url)
    if not m:
        raise ValueError("請貼上單一 Google 試算表的完整網址，不是試算表首頁。")
    sheet_id = m.group(1)
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    fragment = urllib.parse.parse_qs(parsed.fragment)
    gid = query.get("gid", fragment.get("gid", ["0"]))[0]
    if fmt == "xlsx":
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def _column_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref.upper())
    total = 0
    for char in letters.group(0) if letters else "A":
        total = total * 26 + ord(char) - 64
    return total - 1


def _xlsx_rows_and_fills(path: Path) -> tuple[list[list[str]], dict[tuple[int, int], str]]:
    """Read first XLSX worksheet and fill RGB using only Python standard library."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "p": "http://schemas.openxmlformats.org/package/2006/relationships"}
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", ns):
                shared.append("".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")))

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        first_sheet = workbook.find("m:sheets/m:sheet", ns)
        if first_sheet is None:
            return [], {}
        rel_id = first_sheet.attrib.get(f"{{{ns['r']}}}id", "")
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = "worksheets/sheet1.xml"
        for rel in rels:
            if rel.attrib.get("Id") == rel_id:
                target = rel.attrib.get("Target", target).lstrip("/")
                break
        sheet_path = target if target.startswith("xl/") else "xl/" + target

        style_fills: list[int] = []
        fill_colors: list[str] = []
        if "xl/styles.xml" in archive.namelist():
            styles = ET.fromstring(archive.read("xl/styles.xml"))
            fills_node = styles.find("m:fills", ns)
            if fills_node is not None:
                for fill in fills_node:
                    fg = fill.find("m:patternFill/m:fgColor", ns)
                    color = ""
                    if fg is not None:
                        color = fg.attrib.get("rgb", "")[-6:].upper()
                        indexed = fg.attrib.get("indexed")
                        if indexed == "6": color = "FFFF00"
                        if indexed == "8": color = "00FFFF"
                    fill_colors.append(color)
            xfs = styles.find("m:cellXfs", ns)
            if xfs is not None:
                style_fills = [int(xf.attrib.get("fillId", "0")) for xf in xfs]

        root = ET.fromstring(archive.read(sheet_path))
        values: dict[tuple[int, int], str] = {}
        fills: dict[tuple[int, int], str] = {}
        max_row = max_col = -1
        for cell in root.findall(".//m:sheetData/m:row/m:c", ns):
            ref = cell.attrib.get("r", "A1")
            row_match = re.search(r"\d+", ref)
            row_i = int(row_match.group(0)) - 1 if row_match else 0
            col_i = _column_index(ref)
            cell_type = cell.attrib.get("t", "")
            value_node = cell.find("m:v", ns)
            inline = cell.find("m:is", ns)
            value = ""
            if inline is not None:
                value = "".join(node.text or "" for node in inline.iter() if node.tag.endswith("}t"))
            elif value_node is not None:
                raw = value_node.text or ""
                value = shared[int(raw)] if cell_type == "s" and raw.isdigit() and int(raw) < len(shared) else raw
            values[(row_i, col_i)] = value
            style_i = int(cell.attrib.get("s", "0"))
            if style_i < len(style_fills) and style_fills[style_i] < len(fill_colors):
                color = fill_colors[style_fills[style_i]]
                if color:
                    fills[(row_i, col_i)] = color
            max_row, max_col = max(max_row, row_i), max(max_col, col_i)
        rows = [[values.get((r, c), "") for c in range(max_col + 1)] for r in range(max_row + 1)]
        return rows, fills


def read_source(source: str) -> tuple[list[list[str]], dict[tuple[int, int], str]]:
    if source.lower().startswith("http"):
        request = urllib.request.Request(sheet_export_url(source, "xlsx"), headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                data = response.read()
            temp = Path.home() / "Downloads" / "熱血少年_最新預約表.xlsx"
            temp.write_bytes(data)
            return _xlsx_rows_and_fills(temp)
        except Exception as exc:
            raise RuntimeError("這份表格需要使用已登入 Google 的瀏覽器讀取。") from exc

    path = Path(source)
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.reader(handle)), {}
    if path.suffix.lower() == ".xlsx":
        return _xlsx_rows_and_fills(path)
    raise ValueError("目前支援 Google 試算表網址、CSV 或 XLSX。")


def find_column(headers: list[str], keywords: tuple[str, ...]) -> int:
    for index, header in enumerate(headers):
        compact = re.sub(r"\s+", "", header)
        if any(word in compact for word in keywords):
            return index
    raise ValueError(f"找不到欄位：{'／'.join(keywords)}")


def photographer_from_color(rgb: str) -> str:
    rgb = (rgb or "").upper().lstrip("#")[-6:]
    if len(rgb) != 6:
        return "待確認"
    try:
        red, green, blue = int(rgb[:2], 16), int(rgb[2:4], 16), int(rgb[4:], 16)
    except ValueError:
        return "待確認"
    if red >= 180 and green >= 170 and blue <= 150:
        return "植先"
    if blue >= 150 and green >= 140 and red <= 160:
        return "植丞"
    return "待確認"


def parse_matches(rows: list[list[str]], fills: dict[tuple[int, int], str] | None = None) -> list[Match]:
    if not rows:
        return []
    headers = rows[0]
    date_i = find_column(headers, ("比賽日期", "日期"))
    time_i = find_column(headers, ("比賽開始時間", "開始時間"))
    group_i = find_column(headers, ("年級組別", "組別"))
    matchup_i = find_column(headers, ("比賽隊伍名稱", "對戰隊伍", "對戰"))
    team_i = next((i for i, h in enumerate(headers) if "隊伍名稱/球員" in h or "隊伍名稱／球員" in h), -1)
    venue_i = next((i for i, h in enumerate(headers) if "比賽地點" in h or "場地" in h), -1)
    request_i = next((i for i, h in enumerate(headers) if "團體拍攝" in h or "指定球員" in h), -1)
    result: list[Match] = []
    for row_no, row in enumerate(rows[1:], 2):
        try:
            get = lambda i: row[i].strip() if 0 <= i < len(row) and row[i] else ""
            if not get(date_i) or not get(time_i) or not get(matchup_i):
                continue
            day = parse_date(get(date_i))
            matchup = get(matchup_i)
            if not has_match_separator(matchup) and own_team(get(team_i)):
                matchup = f"{own_team(get(team_i))} vs {matchup}"
            matchup = normalize_matchup(matchup)
            photographer = photographer_from_color((fills or {}).get((row_no - 1, group_i), ""))
            result.append(Match(day, parse_start(day, get(time_i)), get(venue_i), get(group_i), matchup, get(request_i), photographer))
        except Exception as exc:
            raise ValueError(f"第 {row_no} 列資料有問題：{exc}") from exc
    return sorted(result, key=lambda item: (item.start, item.venue, item.matchup))


def photo_taken_at(path: Path) -> datetime:
    if path.suffix.lower() in {".jpg", ".jpeg", ".tif", ".tiff"}:
        try:
            from PIL import Image
            with Image.open(path) as image:
                exif = image.getexif()
                value = exif.get(36867) or exif.get(306)
                if value:
                    return datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
        except Exception:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1040x720")
        self.minsize(900, 620)
        self.matches: list[Match] = []
        self.source_var = tk.StringVar()
        self.photo_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.event_var = tk.StringVar(value="桃園獵鷹盃")
        self.mode_var = tk.StringVar(value="copy")
        self.zhixian_var = tk.BooleanVar(value=True)
        self.zhicheng_var = tk.BooleanVar(value=True)
        self._build()

    def _build(self) -> None:
        top = ttk.Frame(self, padding=14)
        top.pack(fill="x")
        ttk.Label(top, text=APP_NAME, font=("Microsoft JhengHei UI", 18, "bold")).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 12))
        ttk.Label(top, text="預約表").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(top, textvariable=self.source_var).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(top, text="貼 Google 網址／自動下載", command=self.ask_google_url).grid(row=1, column=2, sticky="ew", padx=(0, 6))
        ttk.Button(top, text="選擇 CSV/XLSX", command=self.pick_sheet).grid(row=1, column=3, sticky="ew")
        self._row(top, 2, "照片來源", self.photo_var, lambda: self.pick_dir(self.photo_var), "選擇資料夾")
        self._row(top, 3, "輸出位置", self.output_var, lambda: self.pick_dir(self.output_var), "選擇資料夾")
        ttk.Label(top, text="賽事名稱").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Entry(top, textvariable=self.event_var).grid(row=4, column=1, sticky="ew", padx=8)
        ttk.Radiobutton(top, text="複製照片（安全，推薦）", variable=self.mode_var, value="copy").grid(row=4, column=2, sticky="w")
        ttk.Radiobutton(top, text="移動照片", variable=self.mode_var, value="move").grid(row=4, column=3, sticky="w")
        ttk.Label(top, text="攝影師").grid(row=5, column=0, sticky="w", pady=6)
        photographer_box = ttk.Frame(top)
        photographer_box.grid(row=5, column=1, columnspan=3, sticky="w", padx=8)
        ttk.Checkbutton(photographer_box, text="植先（黃色）", variable=self.zhixian_var, command=self.photographer_changed).pack(side="left")
        ttk.Checkbutton(photographer_box, text="植丞（藍色）", variable=self.zhicheng_var, command=self.photographer_changed).pack(side="left", padx=(16, 0))
        top.columnconfigure(1, weight=1)

        buttons = ttk.Frame(self, padding=(14, 0, 14, 8))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="① 讀取預約表", command=self.load_schedule).pack(side="left")
        ttk.Button(buttons, text="② 建立資料夾", command=self.create_folders).pack(side="left", padx=8)
        ttk.Button(buttons, text="③ 自動分類照片", command=self.start_sort).pack(side="left")

        columns = ("photographer", "date", "time", "venue", "group", "matchup", "folder", "status")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        labels = {"photographer":"攝影師", "date":"日期", "time":"時間", "venue":"場地", "group":"組別", "matchup":"對戰", "folder":"資料夾名稱", "status":"狀態"}
        widths = {"photographer":65, "date":90, "time":60, "venue":55, "group":60, "matchup":180, "folder":340, "status":85}
        for col in columns:
            self.tree.heading(col, text=labels[col])
            self.tree.column(col, width=widths[col], anchor="w")
        self.tree.pack(fill="both", expand=True, padx=14)
        self.status = tk.StringVar(value="先貼上 Google 試算表網址，或選擇 CSV／XLSX。")
        ttk.Label(self, textvariable=self.status, padding=14).pack(fill="x")

    def _row(self, parent, row, label, variable, command, button_text):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, columnspan=2, sticky="ew", padx=8)
        ttk.Button(parent, text=button_text, command=command).grid(row=row, column=3, sticky="ew")

    def pick_sheet(self):
        path = filedialog.askopenfilename(filetypes=[("預約表", "*.csv *.xlsx"), ("所有檔案", "*.*")])
        if path:
            self.source_var.set(path)

    def ask_google_url(self):
        current = self.source_var.get().strip()
        initial = current if current.lower().startswith("http") else ""
        url = simpledialog.askstring(
            "Google 預約表網址",
            "請貼上 Google 試算表的完整網址：",
            initialvalue=initial,
            parent=self,
        )
        if url:
            self.source_var.set(url.strip())
            self.google_browser_download()

    def pick_dir(self, variable):
        path = filedialog.askdirectory()
        if path:
            variable.set(path)

    def load_schedule(self):
        source = self.source_var.get().strip()
        if source.lower().startswith("http"):
            self.google_browser_download()
            return
        try:
            rows, fills = read_source(source)
            self.show_rows(rows, fills)
        except Exception as exc:
            messagebox.showerror("讀取失敗", str(exc))

    def selected_names(self):
        names = set()
        if self.zhixian_var.get(): names.add("植先")
        if self.zhicheng_var.get(): names.add("植丞")
        return names

    def effective_photographer(self, match):
        """CSV has no cell colours; one checked photographer is an unambiguous fallback."""
        if match.photographer != "待確認":
            return match.photographer
        names = self.selected_names()
        return next(iter(names)) if len(names) == 1 else "待確認"

    def filtered_matches(self):
        names = self.selected_names()
        if not names:
            return []
        return [m for m in self.matches if m.photographer in names or m.photographer == "待確認"]

    def refresh_view(self):
        if self.matches:
            self.render_matches()

    def photographer_changed(self):
        names = "、".join(sorted(self.selected_names())) or "尚未選擇"
        if self.matches:
            self.render_matches()
        else:
            self.status.set(f"目前攝影師：{names}。讀取預約表後會顯示對應顏色的場次。")

    def show_rows(self, rows, fills=None):
        self.matches = parse_matches(rows, fills)
        if not self.matches:
            raise ValueError("表格內沒有可用的預約場次。")
        self.render_matches()

    def render_matches(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        counts: dict[datetime, int] = {}
        visible = self.filtered_matches()
        for match in visible:
            key = (self.effective_photographer(match), match.start)
            counts[key] = counts.get(key, 0) + 1
        for match in visible:
            photographer = self.effective_photographer(match)
            status = "撞場待確認" if counts[(photographer, match.start)] > 1 else "可分類"
            self.tree.insert("", "end", values=(photographer, match.date.strftime("%Y.%m.%d"), match.start.strftime("%H:%M"), match.venue, match.group, match.matchup, match.folder_name(self.event_var.get().strip()), status))
        unknown = sum(1 for m in self.matches if m.photographer == "待確認")
        fallback = "；CSV 無顏色，已依目前勾選的攝影師顯示" if unknown and len(self.selected_names()) == 1 else ""
        self.status.set(f"共讀到 {len(self.matches)} 場，目前顯示 {len(visible)} 場；未標黃色或藍色：{unknown} 場{fallback}。")

    def google_browser_download(self):
        url = self.source_var.get().strip()
        try:
            export_url = sheet_export_url(url, "xlsx")
        except Exception as exc:
            messagebox.showerror("網址錯誤", str(exc))
            return
        self.status.set("正在自動下載 Google 預約表...")
        threading.Thread(target=self._try_direct_google_download, args=(url, export_url), daemon=True).start()

    def _try_direct_google_download(self, original_url, export_url):
        try:
            request = urllib.request.Request(export_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=25) as response:
                data = response.read()
            if not data.startswith(b"PK"):
                raise ValueError("Google 要求登入")
            downloads = Path.home() / "Downloads"
            downloads.mkdir(parents=True, exist_ok=True)
            target = downloads / "熱血少年_最新預約表.xlsx"
            target.write_bytes(data)
            rows, fills = read_source(str(target))
            self.after(0, self.source_var.set, str(target))
            self.after(0, self.show_rows, rows, fills)
            return
        except Exception:
            self.after(0, self._open_google_in_browser, original_url)

    def _open_google_in_browser(self, original_url):
        export_url = sheet_export_url(original_url, "xlsx")
        downloads = Path.home() / "Downloads"
        before = {p: p.stat().st_mtime for p in downloads.glob("*.xlsx")} if downloads.exists() else {}
        self.status.set("需要 Google 登入，已開啟瀏覽器；程式會自動接回下載的 XLSX...")
        webbrowser.open(export_url)
        threading.Thread(target=self._wait_for_csv, args=(downloads, before), daemon=True).start()

    def _wait_for_csv(self, downloads: Path, before: dict[Path, float]):
        deadline = time.time() + 120
        while time.time() < deadline:
            candidates = []
            if downloads.exists():
                for path in downloads.glob("*.xlsx"):
                    try:
                        if path.stat().st_mtime > before.get(path, 0) and path.stat().st_mtime > time.time() - 150:
                            candidates.append(path)
                    except OSError:
                        pass
            if candidates:
                newest = max(candidates, key=lambda p: p.stat().st_mtime)
                try:
                    rows, fills = read_source(str(newest))
                    self.after(0, self.source_var.set, str(newest))
                    self.after(0, self.show_rows, rows, fills)
                    return
                except Exception:
                    pass
            time.sleep(1)
        self.after(0, self._download_timeout)

    def _download_timeout(self):
        self.status.set("尚未偵測到 XLSX；可按右側「選擇 CSV/XLSX」選取下載檔。")
        messagebox.showinfo("尚未找到下載檔", "瀏覽器若已下載完成，請按右側「選擇 CSV/XLSX」，到下載資料夾選取剛才的 XLSX。")

    def create_folders(self):
        if not self.matches:
            self.load_schedule()
            if not self.matches:
                return
        output_text = self.output_var.get().strip()
        if not output_text:
            messagebox.showwarning("尚未選擇", "請先選擇輸出位置。")
            return
        output = Path(output_text)
        event = self.event_var.get().strip()
        selected = self.filtered_matches()
        if not selected:
            messagebox.showwarning("尚未勾選", "請至少勾選一位攝影師。")
            return
        for match in selected:
            (output / match.folder_name(event)).mkdir(parents=True, exist_ok=True)
        (output / "待確認_同時段或無法判斷").mkdir(parents=True, exist_ok=True)
        self.status.set(f"已建立 {len(selected)} 個場次資料夾。")

    def start_sort(self):
        if not self.matches:
            self.load_schedule()
            if not self.matches:
                return
        selected = self.selected_names()
        if not selected:
            messagebox.showwarning("尚未勾選", "請至少勾選一位攝影師。")
            return
        missing = []
        if not self.photo_var.get(): missing.append("照片來源")
        if not self.output_var.get(): missing.append("輸出位置")
        if missing:
            messagebox.showwarning("資料不足", "請選擇：" + "、".join(missing))
            return
        if self.mode_var.get() == "move" and not messagebox.askyesno("確認移動", "移動後原資料夾不會保留照片。確定要繼續嗎？"):
            return
        threading.Thread(target=self.sort_all_photographers, daemon=True).start()

    def sort_all_photographers(self):
        try:
            output = Path(self.output_var.get()).resolve()
            event = self.event_var.get().strip()
            for match in self.filtered_matches():
                (output / match.folder_name(event)).mkdir(parents=True, exist_ok=True)
            (output / "待確認_同時段或無法判斷").mkdir(parents=True, exist_ok=True)
            source = Path(self.photo_var.get()).resolve()
            done, review = self.sort_selected(source, output, event)
            grand_sorted, grand_review = done, review
            self.after(0, self.status.set, f"完成：自動分類 {grand_sorted} 張，待確認 {grand_review} 張。")
            self.after(0, messagebox.showinfo, "分類完成", f"自動分類：{grand_sorted} 張\n待確認：{grand_review} 張")
        except Exception as exc:
            self.after(0, messagebox.showerror, "分類失敗", str(exc))

    def sort_selected(self, source, output, event):
            names = self.selected_names()
            matches = [m for m in self.matches if m.photographer in names or m.photographer == "待確認"]
            by_start: dict[datetime, list[Match]] = {}
            for match in matches:
                by_start.setdefault(match.start, []).append(match)
            unique = [items[0] for _, items in sorted(by_start.items()) if len(items) == 1]
            files = [p for p in source.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS and output not in p.resolve().parents]
            sorted_count = 0
            review_count = 0
            for index, photo in enumerate(files, 1):
                taken = photo_taken_at(photo)
                candidates = [m for m in unique if m.start.date() == taken.date() and m.start - timedelta(minutes=10) <= taken <= m.start + timedelta(minutes=100)]
                candidates.sort(key=lambda m: abs((taken - m.start).total_seconds()))
                if candidates:
                    chosen = candidates[0]
                    # If another booking starts at the same time, human confirmation is safer.
                    if len(by_start.get(chosen.start, [])) > 1:
                        target_dir = output / "待確認_同時段或無法判斷"
                        review_count += 1
                    else:
                        target_dir = output / chosen.folder_name(event)
                        sorted_count += 1
                else:
                    target_dir = output / "待確認_同時段或無法判斷"
                    review_count += 1
                target_dir.mkdir(parents=True, exist_ok=True)
                destination = unique_destination(target_dir / photo.name)
                if self.mode_var.get() == "move":
                    shutil.move(str(photo), destination)
                else:
                    shutil.copy2(photo, destination)
                if index % 20 == 0:
                    self.after(0, self.status.set, f"照片處理中：{index}/{len(files)}")
            return sorted_count, review_count


if __name__ == "__main__":
    App().mainloop()
