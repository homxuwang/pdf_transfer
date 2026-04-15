import json
import queue
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, W, X, filedialog, messagebox
import tkinter as tk
from tkinter import ttk

from mineru_to_searchable_pdf import main as convert_cli_main


APP_NAME = "MinerU PDF Converter"
CONFIG_NAME = "mineru_gui_config.json"
LOG_DIR_NAME = "logs"
CLI_MODE_FLAG = "--cli-convert"


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def config_path() -> Path:
    return get_app_dir() / CONFIG_NAME


def log_dir_path() -> Path:
    return get_app_dir() / LOG_DIR_NAME


def is_cli_conversion_mode() -> bool:
    return CLI_MODE_FLAG in sys.argv[1:]


def run_cli_conversion() -> int:
    sys.argv = [sys.argv[0], *[arg for arg in sys.argv[1:] if arg != CLI_MODE_FLAG]]
    return convert_cli_main()


class MinerUGuiApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("820x580")
        self.root.minsize(760, 520)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_window_close)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.worker_process: subprocess.Popen[str] | None = None
        self.current_log_path: Path | None = None
        self.stop_requested = False

        self.input_pdf_var = tk.StringVar()
        self.output_pdf_var = tk.StringVar()
        self.token_var = tk.StringVar()
        self.show_token_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="就绪")
        self.log_path_var = tk.StringVar(value="本次运行日志：未开始")

        self._build_ui()
        self._load_config()
        self.root.after(200, self._drain_log_queue)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill=BOTH, expand=True)

        ttk.Label(
            container,
            text="MinerU PDF 转可搜索 PDF",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(anchor=W)

        ttk.Label(
            container,
            text="选择本地 PDF，配置 MinerU Token，生成带文字层的可搜索 PDF。",
        ).pack(anchor=W, pady=(6, 16))

        self._build_path_row(
            container,
            label="PDF 文件",
            textvariable=self.input_pdf_var,
            browse_text="选择 PDF",
            browse_command=self._choose_input_pdf,
        )
        self._build_path_row(
            container,
            label="输出 PDF",
            textvariable=self.output_pdf_var,
            browse_text="另存为",
            browse_command=self._choose_output_pdf,
        )

        token_frame = ttk.LabelFrame(container, text="MinerU Token", padding=12)
        token_frame.pack(fill=X, pady=(14, 0))

        self.token_entry = ttk.Entry(token_frame, textvariable=self.token_var, show="*")
        self.token_entry.pack(side=LEFT, fill=X, expand=True)

        ttk.Checkbutton(
            token_frame,
            text="显示",
            variable=self.show_token_var,
            command=self._toggle_token_visibility,
        ).pack(side=LEFT, padx=(10, 0))

        ttk.Button(token_frame, text="保存 Token", command=self._save_config).pack(side=LEFT, padx=(10, 0))

        actions = ttk.Frame(container)
        actions.pack(fill=X, pady=(14, 0))

        self.start_button = ttk.Button(actions, text="开始转换", command=self._start_conversion)
        self.start_button.pack(side=LEFT)

        self.stop_button = ttk.Button(actions, text="手动停止", command=self._stop_conversion, state="disabled")
        self.stop_button.pack(side=LEFT, padx=(10, 0))

        ttk.Button(actions, text="打开日志目录", command=self._open_logs_dir).pack(side=LEFT, padx=(10, 0))
        ttk.Button(actions, text="打开程序目录", command=self._open_app_dir).pack(side=LEFT, padx=(10, 0))

        ttk.Label(actions, textvariable=self.status_var).pack(side=RIGHT)

        log_meta = ttk.Frame(container)
        log_meta.pack(fill=X, pady=(10, 0))
        ttk.Label(log_meta, textvariable=self.log_path_var).pack(side=LEFT, anchor=W)

        log_frame = ttk.LabelFrame(container, text="运行日志", padding=12)
        log_frame.pack(fill=BOTH, expand=True, pady=(10, 0))

        self.log_text = tk.Text(log_frame, wrap="word", height=20)
        self.log_text.pack(fill=BOTH, expand=True)
        self.log_text.configure(state="disabled")

    def _build_path_row(
        self,
        parent: ttk.Frame,
        label: str,
        textvariable: tk.StringVar,
        browse_text: str,
        browse_command,
    ) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=X, pady=6)

        ttk.Label(frame, text=label, width=10).pack(side=LEFT)
        ttk.Entry(frame, textvariable=textvariable).pack(side=LEFT, fill=X, expand=True)
        ttk.Button(frame, text=browse_text, command=browse_command).pack(side=LEFT, padx=(10, 0))

    def _choose_input_pdf(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 PDF 文件",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")],
        )
        if not path:
            return
        self.input_pdf_var.set(path)
        if not self.output_pdf_var.get().strip():
            input_path = Path(path)
            self.output_pdf_var.set(str(input_path.with_name(f"{input_path.stem}.searchable.pdf")))
        self._save_config(silent=True)

    def _choose_output_pdf(self) -> None:
        initial = self.output_pdf_var.get().strip()
        path = filedialog.asksaveasfilename(
            title="选择输出 PDF",
            defaultextension=".pdf",
            initialfile=Path(initial).name if initial else "",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")],
        )
        if path:
            self.output_pdf_var.set(path)
            self._save_config(silent=True)

    def _toggle_token_visibility(self) -> None:
        self.token_entry.configure(show="" if self.show_token_var.get() else "*")

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.log_text.configure(state="normal")
        self.log_text.insert(END, f"{line}\n")
        self.log_text.see(END)
        self.log_text.configure(state="disabled")

        if self.current_log_path:
            try:
                self.current_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.current_log_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"{line}\n")
            except Exception:
                pass

    def _queue_log(self, message: str) -> None:
        self.log_queue.put(message.rstrip())

    def _drain_log_queue(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if message:
                self._append_log(message)
        self.root.after(200, self._drain_log_queue)

    def _load_config(self) -> None:
        path = config_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self._append_log(f"配置文件读取失败: {path}")
            return

        self.token_var.set(data.get("token", ""))
        self.input_pdf_var.set(data.get("last_input_pdf", ""))
        self.output_pdf_var.set(data.get("last_output_pdf", ""))

    def _save_config(self, silent: bool = False) -> None:
        data = {
            "token": self.token_var.get().strip(),
            "last_input_pdf": self.input_pdf_var.get().strip(),
            "last_output_pdf": self.output_pdf_var.get().strip(),
        }
        path = config_path()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if not silent:
            self._append_log(f"已保存配置: {path}")

    def _validate_inputs(self) -> bool:
        input_pdf = self.input_pdf_var.get().strip()
        output_pdf = self.output_pdf_var.get().strip()
        token = self.token_var.get().strip()

        if not input_pdf:
            messagebox.showerror(APP_NAME, "请先选择本地 PDF 文件。")
            return False
        if not Path(input_pdf).exists():
            messagebox.showerror(APP_NAME, "选择的 PDF 文件不存在。")
            return False
        if not output_pdf:
            messagebox.showerror(APP_NAME, "请设置输出 PDF 路径。")
            return False
        if not token:
            messagebox.showerror(APP_NAME, "请填写 MinerU Token。")
            return False
        return True

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.status_var.set("处理中..." if running else "就绪")

    def _create_run_log_path(self) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return log_dir_path() / f"mineru_run_{timestamp}.log"

    def _build_command(self, input_pdf: str, output_pdf: str, token: str) -> list[str]:
        if getattr(sys, "frozen", False):
            base = [str(Path(sys.executable).resolve())]
        else:
            base = [sys.executable, str(Path(__file__).resolve())]
        return [
            *base,
            CLI_MODE_FLAG,
            input_pdf,
            "--token",
            token,
            "--output-pdf",
            output_pdf,
        ]

    def _start_conversion(self) -> None:
        if self.worker_process and self.worker_process.poll() is None:
            return
        if not self._validate_inputs():
            return

        self._save_config(silent=True)
        self.stop_requested = False
        self.current_log_path = self._create_run_log_path()
        self.log_path_var.set(f"本次运行日志：{self.current_log_path}")
        self._set_running(True)
        self._append_log("开始执行转换任务。")
        self._append_log(f"日志文件：{self.current_log_path}")

        input_pdf = self.input_pdf_var.get().strip()
        output_pdf = self.output_pdf_var.get().strip()
        token = self.token_var.get().strip()
        command = self._build_command(input_pdf, output_pdf, token)

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.worker_process = subprocess.Popen(
            command,
            cwd=str(get_app_dir()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )

        self.worker_thread = threading.Thread(target=self._stream_process_output, daemon=True)
        self.worker_thread.start()

    def _stream_process_output(self) -> None:
        process = self.worker_process
        if process is None:
            return

        if process.stdout is not None:
            for line in process.stdout:
                self._queue_log(line)

        returncode = process.wait()
        self.root.after(0, lambda: self._handle_process_exit(returncode))

    def _terminate_process_tree(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if sys.platform.startswith("win"):
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=creationflags,
            )
            return
        process.terminate()

    def _stop_conversion(self) -> None:
        process = self.worker_process
        if process is None or process.poll() is not None:
            return

        self.stop_requested = True
        self.status_var.set("正在停止...")
        self._append_log("收到手动停止请求，正在终止当前任务。")
        try:
            self._terminate_process_tree(process)
        except Exception as exc:
            self._append_log(f"终止进程失败: {exc}")

    def _handle_process_exit(self, returncode: int) -> None:
        self._set_running(False)
        output_pdf = self.output_pdf_var.get().strip()
        self.worker_process = None

        if self.stop_requested:
            self.stop_requested = False
            self._append_log("任务已手动停止。")
            messagebox.showwarning(APP_NAME, "任务已手动停止。")
            return

        if returncode == 0:
            self._append_log("转换完成。")
            messagebox.showinfo(APP_NAME, f"转换完成。\n输出文件:\n{output_pdf}")
            return

        self._append_log(f"转换失败，退出码: {returncode}")
        if self.current_log_path:
            messagebox.showerror(
                APP_NAME,
                f"转换失败。\n请查看日志文件：\n{self.current_log_path}",
            )
        else:
            messagebox.showerror(APP_NAME, f"转换失败，退出码: {returncode}")

    def _open_logs_dir(self) -> None:
        path = log_dir_path()
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    def _open_app_dir(self) -> None:
        self._open_path(get_app_dir())

    def _open_path(self, path: Path) -> None:
        try:
            import os

            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"无法打开目录:\n{exc}")

    def _handle_window_close(self) -> None:
        process = self.worker_process
        if process is not None and process.poll() is None:
            try:
                self._terminate_process_tree(process)
            except Exception:
                pass
        self.root.destroy()


def run_gui() -> None:
    root = tk.Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    MinerUGuiApp(root)
    root.mainloop()


def main() -> int:
    if is_cli_conversion_mode():
        return run_cli_conversion()
    run_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
