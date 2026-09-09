"""Tkinter GUI launcher for the existing Pokémon GO cleanup CLI.

Run from the project virtual environment:

    python -m pokemon_go_cleanup.gui

The GUI stays inside WSL/Ubuntu and launches the existing CLI commands in a
child process, so automation, OCR, CSV writing, and safety checks remain in the
current implementation.
"""

from __future__ import annotations

import csv
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Final

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
except ModuleNotFoundError as error:
    raise SystemExit(
        "Tkinter 尚未安装。请在 Ubuntu 中运行：\n"
        "sudo apt update && sudo apt install -y python3-tk"
    ) from error


PROJECT_ROOT: Final = Path(__file__).resolve().parents[2]
DEFAULT_CSV: Final = PROJECT_ROOT / "inventory.csv"
SCAN_ROOT: Final = PROJECT_ROOT / "data" / "scans"


def count_csv_data_rows(path: Path) -> int | None:
    """Return data-row count for one readable CSV, or ``None`` while unavailable."""

    try:
        with path.open("r", encoding="utf-8", newline="") as source:
            reader = csv.reader(source)
            if next(reader, None) is None:
                return 0
            return sum(1 for row in reader if row)
    except (OSError, UnicodeError, csv.Error):
        return None


def format_elapsed_time(elapsed_seconds: float) -> str:
    """Format a non-negative elapsed duration as hours, minutes, and seconds."""

    total_seconds = max(0, int(elapsed_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_iv_only_progress(line: str) -> int | None:
    """Read the completed count from one stable IV-only CLI progress line."""

    if not line.startswith("IV_ONLY_PROGRESS "):
        return None
    fields = line.split(maxsplit=2)
    if len(fields) < 2 or "/" not in fields[1]:
        return None
    completed_text, _ = fields[1].split("/", maxsplit=1)
    try:
        completed = int(completed_text)
    except ValueError:
        return None
    return completed if completed >= 0 else None


class PokemonGoCleanupGui(tk.Tk):
    """Small WSL GUI that launches the existing command-line application."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Pokémon GO Cleanup")
        self.geometry("860x760")
        self.minsize(720, 690)

        self._process: subprocess.Popen[str] | None = None
        self._worker: threading.Thread | None = None
        self._messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self._current_task = ""
        self._batch_progress_csv: Path | None = None
        self._batch_baseline_rows = 0
        self._scan_started_at: float | None = None

        self._limit_var = tk.StringVar(value="5")
        self._delay_var = tk.StringVar(value="2")
        self._csv_var = tk.StringVar(value=str(DEFAULT_CSV))
        self._debug_var = tk.BooleanVar(value=True)
        self._resume_var = tk.BooleanVar(value=False)
        self._rename_with_iv_var = tk.BooleanVar(value=False)
        self._status_var = tk.StringVar(value="就绪")
        self._successful_scans_var = tk.StringVar(value="0")
        self._elapsed_time_var = tk.StringVar(value="00:00:00")
        self._device_var = tk.StringVar(value="尚未检查手机")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_messages)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.configure("Title.TLabel", font=("", 18, "bold"))
        style.configure("Section.TLabelframe.Label", font=("", 11, "bold"))
        style.configure("Status.TLabel", font=("", 10, "bold"))
        style.configure("Counter.TLabel", font=("", 18, "bold"))
        style.configure("Timer.TLabel", font=("", 12, "bold"))

        outer = ttk.Frame(self, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            outer,
            text="Pokémon GO Cleanup",
            style="Title.TLabel",
        ).pack(anchor=tk.W)
        ttk.Label(
            outer,
            text="华为 Mate 30 · 1440×3120 · 现有安全扫描流程的图形启动器",
        ).pack(anchor=tk.W, pady=(2, 12))

        device_frame = ttk.LabelFrame(
            outer,
            text="手机连接",
            style="Section.TLabelframe",
            padding=10,
        )
        device_frame.pack(fill=tk.X)

        device_row = ttk.Frame(device_frame)
        device_row.pack(fill=tk.X)
        ttk.Label(device_row, textvariable=self._device_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        self._check_button = ttk.Button(
            device_row,
            text="检查手机",
            command=self._check_device,
        )
        self._check_button.pack(side=tk.RIGHT)

        settings = ttk.LabelFrame(
            outer,
            text="批量扫描设置",
            style="Section.TLabelframe",
            padding=10,
        )
        settings.pack(fill=tk.X, pady=(12, 0))
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="最多处理数量").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 10), pady=5
        )
        ttk.Spinbox(
            settings,
            from_=1,
            to=9999,
            textvariable=self._limit_var,
            width=12,
        ).grid(row=0, column=1, sticky=tk.W, pady=5)

        scan_progress = ttk.Frame(settings)
        scan_progress.grid(
            row=0,
            column=2,
            rowspan=2,
            sticky=tk.NE,
            padx=(24, 0),
            pady=5,
        )
        scan_count = ttk.Frame(scan_progress)
        scan_count.pack(anchor=tk.E)
        ttk.Label(scan_count, text="本次已成功处理").pack(side=tk.LEFT)
        ttk.Label(
            scan_count,
            textvariable=self._successful_scans_var,
            style="Counter.TLabel",
        ).pack(side=tk.LEFT, padx=(10, 4))
        ttk.Label(scan_count, text="只").pack(side=tk.LEFT)

        scan_timer = ttk.Frame(scan_progress)
        scan_timer.pack(anchor=tk.E, pady=(4, 0))
        ttk.Label(scan_timer, text="本次已用时间").pack(side=tk.LEFT)
        ttk.Label(
            scan_timer,
            textvariable=self._elapsed_time_var,
            style="Timer.TLabel",
        ).pack(side=tk.LEFT, padx=(10, 0))

        ttk.Label(settings, text="切换前等待秒数").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 10), pady=5
        )
        ttk.Spinbox(
            settings,
            from_=0,
            to=120,
            increment=0.5,
            textvariable=self._delay_var,
            width=12,
        ).grid(row=1, column=1, sticky=tk.W, pady=5)

        ttk.Label(settings, text="CSV 文件").grid(
            row=2, column=0, sticky=tk.W, padx=(0, 10), pady=5
        )
        csv_row = ttk.Frame(settings)
        csv_row.grid(row=2, column=1, sticky=tk.EW, pady=5)
        csv_row.columnconfigure(0, weight=1)
        ttk.Entry(csv_row, textvariable=self._csv_var).grid(
            row=0, column=0, sticky=tk.EW
        )
        ttk.Button(
            csv_row,
            text="选择",
            command=self._choose_csv,
        ).grid(row=0, column=1, padx=(8, 0))

        options = ttk.Frame(settings)
        options.grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=(6, 2))
        scan_options = ttk.Frame(options)
        scan_options.pack(anchor=tk.W)
        ttk.Checkbutton(
            scan_options,
            text="保存调试截图和状态记录",
            variable=self._debug_var,
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(
            scan_options,
            text="续接已有 CSV",
            variable=self._resume_var,
        ).pack(side=tk.LEFT, padx=(18, 0))
        ttk.Checkbutton(
            options,
            text="重置中文名后追加 IV（例如 呆火鱷⑮⑮⑮）",
            variable=self._rename_with_iv_var,
        ).pack(anchor=tk.W, pady=(6, 0))

        actions = ttk.LabelFrame(
            outer,
            text="操作",
            style="Section.TLabelframe",
            padding=10,
        )
        actions.pack(fill=tk.X, pady=(12, 0))

        button_row = ttk.Frame(actions)
        button_row.pack(fill=tk.X)

        self._dry_run_button = ttk.Button(
            button_row,
            text="检查当前页面（不操作）",
            command=self._dry_run,
        )
        self._dry_run_button.pack(side=tk.LEFT)

        self._one_button = ttk.Button(
            button_row,
            text="扫描一只",
            command=self._scan_one,
        )
        self._one_button.pack(side=tk.LEFT, padx=(8, 0))

        self._batch_button = ttk.Button(
            button_row,
            text="开始批量扫描",
            command=self._scan_batch,
        )
        self._batch_button.pack(side=tk.LEFT, padx=(8, 0))

        self._stop_button = ttk.Button(
            button_row,
            text="停止",
            command=self._stop_process,
            state=tk.DISABLED,
        )
        self._stop_button.pack(side=tk.LEFT, padx=(8, 0))

        iv_row = ttk.Frame(actions)
        iv_row.pack(fill=tk.X, pady=(10, 0))
        self._iv_name_button = ttk.Button(
            iv_row,
            text="扫描 IV 并命名",
            command=self._rename_iv_one,
        )
        self._iv_name_button.pack(side=tk.LEFT)
        self._iv_batch_button = ttk.Button(
            iv_row,
            text="批量扫描 IV 并命名",
            command=self._rename_iv_batch,
        )
        self._iv_batch_button.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(iv_row, text="不扫描技能 · 不保存 CSV 或扫描文件").pack(
            side=tk.LEFT, padx=(10, 0)
        )

        open_row = ttk.Frame(actions)
        open_row.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(
            open_row,
            text="打开 CSV",
            command=self._open_csv,
        ).pack(side=tk.LEFT)
        ttk.Button(
            open_row,
            text="打开最近扫描",
            command=self._open_latest_scan,
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            open_row,
            text="清空日志",
            command=self._clear_log,
        ).pack(side=tk.RIGHT)

        status_row = ttk.Frame(outer, padding=(10, 8))
        status_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(status_row, text="状态：").pack(side=tk.LEFT)
        ttk.Label(
            status_row,
            textvariable=self._status_var,
            style="Status.TLabel",
        ).pack(side=tk.LEFT)

        log_frame = ttk.LabelFrame(
            outer,
            text="运行日志",
            style="Section.TLabelframe",
            padding=8,
        )
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        self._log = scrolledtext.ScrolledText(
            log_frame,
            width=1,
            height=8,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("TkFixedFont", 10),
        )
        self._log.pack(fill=tk.BOTH, expand=True)

    def _choose_csv(self) -> None:
        current = Path(self._csv_var.get()).expanduser()
        initial_directory = current.parent if current.parent.exists() else PROJECT_ROOT
        selected = filedialog.asksaveasfilename(
            title="选择批量 CSV",
            initialdir=initial_directory,
            initialfile=current.name or "inventory.csv",
            defaultextension=".csv",
            filetypes=(("CSV 文件", "*.csv"), ("所有文件", "*.*")),
        )
        if selected:
            self._csv_var.set(selected)

    def _check_device(self) -> None:
        self._start_command(
            [sys.executable, "-m", "pokemon_go_cleanup", "device", "info"],
            task="device",
            heading="检查手机连接",
        )

    def _dry_run(self) -> None:
        command = [
            sys.executable,
            "-m",
            "pokemon_go_cleanup",
            "scan-auto-one",
            "--dry-run",
        ]
        if self._debug_var.get():
            command.append("--debug")
        if self._rename_with_iv_var.get():
            command.append("--rename-with-iv")
        self._start_command(command, task="dry-run", heading="检查当前详情页")

    def _scan_one(self) -> None:
        command = [
            sys.executable,
            "-m",
            "pokemon_go_cleanup",
            "scan-auto-one",
        ]
        if self._debug_var.get():
            command.append("--debug")
        if self._rename_with_iv_var.get():
            command.append("--rename-with-iv")
        self._start_command(command, task="scan-one", heading="自动扫描一只宝可梦")

    def _rename_iv_one(self) -> None:
        self._start_command(
            [sys.executable, "-m", "pokemon_go_cleanup", "rename-iv-one"],
            task="rename-iv-one",
            heading="扫描当前一只的 IV 并命名（不保存文件）",
        )

    def _rename_iv_batch(self) -> None:
        bounds = self._read_batch_bounds()
        if bounds is None:
            return
        limit, delay = bounds
        self._start_command(
            [
                sys.executable,
                "-m",
                "pokemon_go_cleanup",
                "rename-iv-batch",
                "--limit",
                str(limit),
                "--delay",
                str(delay),
            ],
            task="rename-iv-batch",
            heading="批量扫描 IV 并命名（不保存文件）",
        )

    def _read_batch_bounds(self) -> tuple[int, float] | None:
        try:
            limit = int(self._limit_var.get())
        except ValueError:
            messagebox.showerror("输入错误", "最多处理数量必须是整数。")
            return None
        if limit <= 0:
            messagebox.showerror("输入错误", "最多处理数量必须大于 0。")
            return None

        try:
            delay = float(self._delay_var.get())
        except ValueError:
            messagebox.showerror("输入错误", "等待秒数必须是数字。")
            return None
        if not 0 <= delay <= 120:
            messagebox.showerror("输入错误", "等待秒数必须在 0 到 120 之间。")
            return None
        return limit, delay

    def _scan_batch(self) -> None:
        bounds = self._read_batch_bounds()
        if bounds is None:
            return
        limit, delay = bounds

        raw_csv = self._csv_var.get().strip()
        if not raw_csv:
            messagebox.showerror("输入错误", "请选择 CSV 文件。")
            return

        csv_path = Path(raw_csv).expanduser()
        resume = self._resume_var.get()

        if csv_path.exists() and not resume:
            messagebox.showwarning(
                "CSV 已存在",
                "这个 CSV 已经存在。\n\n"
                "请勾选“续接已有 CSV”，或者换一个新的文件名。",
            )
            return
        if resume and not csv_path.exists():
            messagebox.showwarning(
                "找不到 CSV",
                "已经勾选“续接已有 CSV”，但所选文件不存在。",
            )
            return

        command = [
            sys.executable,
            "-m",
            "pokemon_go_cleanup",
            "scan-batch",
            "--limit",
            str(limit),
            "--csv",
            str(csv_path),
            "--delay",
            str(delay),
        ]
        if self._debug_var.get():
            command.append("--debug")
        if self._rename_with_iv_var.get():
            command.append("--rename-with-iv")
        if resume:
            command.append("--resume")
        self._start_command(
            command,
            task="scan-batch",
            heading="开始批量扫描",
            progress_csv=csv_path,
        )

    def _start_command(
        self,
        command: list[str],
        *,
        task: str,
        heading: str,
        progress_csv: Path | None = None,
    ) -> None:
        if self._process is not None:
            messagebox.showinfo("正在运行", "已有一个任务正在运行。")
            return

        self._current_task = task
        if task in ("scan-one", "scan-batch", "rename-iv-one", "rename-iv-batch"):
            self._successful_scans_var.set("0")
            self._elapsed_time_var.set("00:00:00")
        self._batch_progress_csv = progress_csv
        baseline_rows = count_csv_data_rows(progress_csv) if progress_csv is not None else None
        self._batch_baseline_rows = baseline_rows or 0
        self._append_log("")
        self._append_log(f"===== {heading} =====")
        self._append_log("$ " + " ".join(command))
        self._status_var.set("运行中")
        self._set_running(True)

        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"

        try:
            self._process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=True,
            )
        except OSError as error:
            self._process = None
            self._scan_started_at = None
            self._set_running(False)
            self._status_var.set("启动失败")
            messagebox.showerror("启动失败", str(error))
            return

        if task in ("scan-one", "scan-batch", "rename-iv-one", "rename-iv-batch"):
            self._scan_started_at = time.monotonic()

        process = self._process
        self._worker = threading.Thread(
            target=self._read_process_output,
            args=(process,),
            daemon=True,
        )
        self._worker.start()

    def _read_process_output(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                self._messages.put(("line", line.rstrip("\n")))
            return_code = process.wait()
            self._messages.put(("done", return_code))
        except Exception as error:  # GUI boundary: surface worker failures.
            self._messages.put(("worker-error", str(error)))

    def _poll_messages(self) -> None:
        try:
            while True:
                kind, payload = self._messages.get_nowait()
                if kind == "line":
                    self._handle_process_line(str(payload))
                elif kind == "done":
                    if not isinstance(payload, int):
                        self._handle_worker_error(
                            f"无效的进程退出码: {payload!r}"
                        )
                        continue
                    self._handle_process_done(payload)
                elif kind == "worker-error":
                    self._handle_worker_error(str(payload))
        except queue.Empty:
            pass
        self._refresh_batch_success_count()
        self._refresh_scan_elapsed_time()
        self.after(100, self._poll_messages)

    def _handle_process_line(self, line: str) -> None:
        self._append_log(line)
        if self._current_task != "rename-iv-batch":
            return
        completed = parse_iv_only_progress(line)
        if completed is not None:
            self._successful_scans_var.set(str(completed))

    def _handle_process_done(self, return_code: int) -> None:
        task = self._current_task
        self._refresh_batch_success_count()
        self._refresh_scan_elapsed_time()
        if task in ("scan-one", "scan-batch", "rename-iv-one", "rename-iv-batch"):
            self._scan_started_at = None
        if task in ("scan-one", "rename-iv-one") and return_code == 0:
            self._successful_scans_var.set("1")
        self._process = None
        self._worker = None
        self._current_task = ""
        self._set_running(False)

        if return_code == 0:
            self._status_var.set("完成")
            self._append_log("===== 完成 =====")
            if task == "device":
                self._device_var.set("● 手机已连接，ADB 可用")
        elif return_code in (130, -signal.SIGINT):
            self._status_var.set("已停止")
            self._append_log("===== 已停止 =====")
        else:
            self._status_var.set(f"失败（退出码 {return_code}）")
            self._append_log(f"===== 失败：退出码 {return_code} =====")
            if task == "device":
                self._device_var.set("● 未能连接手机，请查看日志")

    def _refresh_batch_success_count(self) -> None:
        if self._current_task != "scan-batch" or self._batch_progress_csv is None:
            return
        current_rows = count_csv_data_rows(self._batch_progress_csv)
        if current_rows is None:
            return
        successful_rows = max(0, current_rows - self._batch_baseline_rows)
        self._successful_scans_var.set(str(successful_rows))

    def _refresh_scan_elapsed_time(self) -> None:
        if self._scan_started_at is None:
            return
        elapsed_seconds = time.monotonic() - self._scan_started_at
        self._elapsed_time_var.set(format_elapsed_time(elapsed_seconds))

    def _handle_worker_error(self, detail: str) -> None:
        self._append_log(f"GUI 读取日志失败：{detail}")
        self._status_var.set("GUI 日志错误")

    def _stop_process(self) -> None:
        process = self._process
        if process is None:
            return

        self._status_var.set("正在停止")
        self._append_log("正在发送 Ctrl+C，请等待当前程序安全收尾……")
        try:
            if sys.platform == "win32":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            return
        except OSError as error:
            self._append_log(f"发送停止信号失败：{error}")

    def _set_running(self, running: bool) -> None:
        state = tk.DISABLED if running else tk.NORMAL
        self._check_button.configure(state=state)
        self._dry_run_button.configure(state=state)
        self._one_button.configure(state=state)
        self._iv_name_button.configure(state=state)
        self._iv_batch_button.configure(state=state)
        self._batch_button.configure(state=state)
        self._stop_button.configure(
            state=tk.NORMAL if running else tk.DISABLED
        )

    def _append_log(self, text: str) -> None:
        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, text + "\n")
        self._log.see(tk.END)
        self._log.configure(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self._log.configure(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.configure(state=tk.DISABLED)

    def _open_csv(self) -> None:
        path = Path(self._csv_var.get()).expanduser()
        if not path.is_file():
            messagebox.showwarning("文件不存在", f"找不到 CSV：\n{path}")
            return
        self._open_with_windows(path)

    def _open_latest_scan(self) -> None:
        candidates = [
            path
            for date_directory in SCAN_ROOT.glob("*")
            if date_directory.is_dir()
            for path in date_directory.glob("*")
            if path.is_dir()
        ]
        if not candidates:
            messagebox.showwarning("没有扫描结果", f"没有找到扫描目录：\n{SCAN_ROOT}")
            return
        latest = max(candidates, key=lambda path: path.stat().st_mtime)
        self._open_with_windows(latest)

    def _open_with_windows(self, path: Path) -> None:
        resolved = path.resolve()
        try:
            converted = subprocess.run(
                ["wslpath", "-w", str(resolved)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            ).stdout.strip()
            if resolved.is_dir():
                subprocess.Popen(["explorer.exe", converted])
            else:
                subprocess.Popen(["cmd.exe", "/C", "start", "", converted])
        except (OSError, subprocess.CalledProcessError) as error:
            messagebox.showerror(
                "无法打开",
                f"无法用 Windows 打开：\n{resolved}\n\n{error}",
            )

    def _on_close(self) -> None:
        if self._process is None:
            self.destroy()
            return

        close = messagebox.askyesno(
            "任务仍在运行",
            "扫描仍在运行。是否先发送停止信号并关闭窗口？",
        )
        if not close:
            return
        self._stop_process()
        self.after(500, self.destroy)


def main() -> None:
    """Start the Tkinter application."""

    app = PokemonGoCleanupGui()
    app.mainloop()


if __name__ == "__main__":
    main()
