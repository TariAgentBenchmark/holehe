import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import httpx
import trio
from openpyxl import Workbook

from holehe.core import is_email
from holehe.modules.shopping.amazon import amazon


async def run_amazon_lookup(email: str, timeout: int):
    """Run the Amazon lookup and return the first result dict."""
    out = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        await amazon(email, client, out)
    return out[0] if out else None

async def run_batch_lookup(pairs, timeout: int, concurrency: int, progress_callback=None):
    """Run multiple Amazon lookups with limited concurrency, preserving order."""
    results = [None] * len(pairs)
    done = 0

    async def worker(idx, email, password, client, limiter):
        async with limiter:
            out = []
            try:
                await amazon(email, client, out)
            except Exception:
                out.append(
                    {
                        "name": "amazon",
                        "domain": "amazon.com",
                        "rateLimit": True,
                        "exists": False,
                        "emailrecovery": None,
                        "phoneNumber": None,
                        "others": None,
                    }
                )
            result = out[0] if out else None
            results[idx] = (email, password, result)
            if progress_callback:
                nonlocal done
                done += 1
                progress_callback(done, len(pairs))

    async with httpx.AsyncClient(timeout=timeout) as client:
        limiter = trio.CapacityLimiter(max(1, concurrency))
        async with trio.open_nursery() as nursery:
            for idx, (email, password) in enumerate(pairs):
                nursery.start_soon(worker, idx, email, password, client, limiter)

    return results


def format_result(email: str, result: dict) -> str:
    if result is None:
        return f"{email}\n状态：未收到响应。"

    if result.get("rateLimit"):
        return f"{email}\n状态：{result.get('domain', 'amazon.com')} 请求受限或失败。"

    exists = result.get("exists")
    status = "账号存在" if exists else "未找到账号"
    lines = [
        f"邮箱：{email}",
        f"站点：{result.get('domain', 'amazon.com')}",
        f"状态：{status}",
    ]

    if result.get("emailrecovery"):
        lines.append(f"找回邮箱：{result['emailrecovery']}")
    if result.get("phoneNumber"):
        lines.append(f"找回电话：{result['phoneNumber']}")
    if result.get("others"):
        lines.append(f"其他信息：{result['others']}")

    return "\n".join(lines)

def format_batch_result(email: str, password: str, result: dict) -> str:
    header = f"{email}:{password}"
    formatted = format_result(email, result)
    return f"{header}\n{formatted.splitlines()[-1]}"


class AmazonUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Holehe 亚马逊邮箱检测")
        self.root.resizable(False, False)

        self.email_var = tk.StringVar()
        self.timeout_var = tk.StringVar(value="10")
        self.concurrency_var = tk.StringVar(value="5")
        self.file_path = tk.StringVar(value="未选择文件")
        self.last_batch_results = []

        self._build_layout()

    def _build_layout(self):
        padding = {"padx": 12, "pady": 8}

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=0, column=0, sticky="nsew")

        # 单条检测 tab
        single_frame = ttk.Frame(notebook)
        notebook.add(single_frame, text="单条检测")

        ttk.Label(single_frame, text="目标邮箱").grid(row=0, column=0, sticky="w", **padding)
        email_entry = ttk.Entry(single_frame, textvariable=self.email_var, width=32)
        email_entry.grid(row=0, column=1, sticky="ew", **padding)
        email_entry.focus()

        ttk.Label(single_frame, text="超时时间（秒）").grid(row=1, column=0, sticky="w", **padding)
        timeout_entry_single = ttk.Entry(single_frame, textvariable=self.timeout_var, width=8)
        timeout_entry_single.grid(row=1, column=1, sticky="w", **padding)

        self.status_var_single = tk.StringVar(value="就绪")
        self.single_result_box = tk.Text(single_frame, width=60, height=12, state="disabled")
        self.single_result_box.grid(row=3, column=0, columnspan=2, sticky="nsew", **padding)

        self.run_button = ttk.Button(single_frame, text="检测单个邮箱", command=self.start_lookup)
        self.run_button.grid(row=2, column=0, columnspan=2, sticky="ew", **padding)

        ttk.Label(single_frame, textvariable=self.status_var_single).grid(row=4, column=0, columnspan=2, sticky="w", **padding)

        single_frame.columnconfigure(1, weight=1)

        # 批量检测 tab
        batch_frame = ttk.Frame(notebook)
        notebook.add(batch_frame, text="批量检测")

        ttk.Label(batch_frame, text="超时时间（秒）").grid(row=0, column=0, sticky="w", **padding)
        timeout_entry_batch = ttk.Entry(batch_frame, textvariable=self.timeout_var, width=8)
        timeout_entry_batch.grid(row=0, column=1, sticky="w", **padding)

        ttk.Label(batch_frame, text="并发数").grid(row=1, column=0, sticky="w", **padding)
        concurrency_entry = ttk.Entry(batch_frame, textvariable=self.concurrency_var, width=8)
        concurrency_entry.grid(row=1, column=1, sticky="w", **padding)

        file_frame = ttk.Frame(batch_frame)
        file_frame.grid(row=2, column=0, columnspan=2, sticky="ew", **padding)
        ttk.Button(file_frame, text="选择批量文件", command=self.select_file).grid(row=0, column=0, sticky="w")
        ttk.Label(file_frame, textvariable=self.file_path, width=45).grid(row=0, column=1, sticky="w", padx=8)

        self.batch_button = ttk.Button(batch_frame, text="运行批量检测", command=self.start_batch)
        self.batch_button.grid(row=3, column=0, columnspan=2, sticky="ew", **padding)

        self.progress = ttk.Progressbar(batch_frame, mode="determinate")
        self.progress.grid(row=4, column=0, columnspan=2, sticky="ew", **padding)

        self.save_button = ttk.Button(
            batch_frame,
            text="保存批量结果为 Excel",
            command=self.save_batch_excel,
            state="disabled",
        )
        self.save_button.grid(row=5, column=0, columnspan=2, sticky="ew", **padding)

        self.status_var_batch = tk.StringVar(value="就绪")
        self.batch_result_box = tk.Text(batch_frame, width=60, height=10, state="disabled")
        self.batch_result_box.grid(row=6, column=0, columnspan=2, sticky="nsew", **padding)
        ttk.Label(batch_frame, textvariable=self.status_var_batch).grid(row=7, column=0, columnspan=2, sticky="w", **padding)

        ttk.Label(
            self.root,
            text="平台抽成高，后续合作可添加微信15637899910。请不要在群里说，如有意向请直接添加微信",
            foreground="gray",
            font=("TkDefaultFont", 10),
            wraplength=400,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))

        batch_frame.columnconfigure(1, weight=1)
        batch_frame.rowconfigure(6, weight=1)
        single_frame.rowconfigure(3, weight=1)

    def start_lookup(self):
        email = self.email_var.get().strip()
        if not email:
            messagebox.showerror("输入错误", "请输入邮箱地址。")
            return
        if not is_email(email):
            messagebox.showerror("输入错误", "请输入有效的邮箱地址。")
            return

        try:
            timeout = int(self.timeout_var.get())
        except ValueError:
            messagebox.showerror("输入错误", "超时时间必须是整数（秒）。")
            return

        self.run_button.state(["disabled"])
        self.status_var_single.set("检测中...")
        self._write_result("正在查询...", self.single_result_box)

        thread = threading.Thread(
            target=self._lookup_worker, args=(email, timeout), daemon=True
        )
        thread.start()

    def _lookup_worker(self, email: str, timeout: int):
        try:
            result = trio.run(run_amazon_lookup, email, timeout)
            formatted = format_result(email, result)
            self.root.after(0, self._on_result, formatted)
        except Exception as exc:  # pragma: no cover - UI feedback path
            self.root.after(0, self._on_error, exc)

    def _on_result(self, formatted: str):
        self._write_result(formatted, self.single_result_box)
        self.status_var_single.set("完成")
        self.run_button.state(["!disabled"])

    def _on_error(self, exc: Exception):
        messagebox.showerror("检测错误", str(exc))
        self.status_var_single.set("出错")
        self.run_button.state(["!disabled"])

    def _write_result(self, text: str, box: tk.Text):
        box.configure(state="normal")
        box.delete("1.0", tk.END)
        box.insert(tk.END, text)
        box.configure(state="disabled")

    def select_file(self):
        path = filedialog.askopenfilename(
            title="选择 txt 文件",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.file_path.set(path)

    def start_batch(self):
        path = self.file_path.get()
        if not path or path == "未选择文件":
            messagebox.showerror("输入错误", "请选择 txt 文件。")
            return

        pairs = self._parse_pairs(path)
        if not pairs:
            messagebox.showerror("输入错误", "未找到有效行（格式应为 email:password）。")
            return

        try:
            timeout = int(self.timeout_var.get())
        except ValueError:
            messagebox.showerror("输入错误", "超时时间必须是整数（秒）。")
            return

        try:
            concurrency = int(self.concurrency_var.get())
            if concurrency < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("输入错误", "并发数必须是大于等于1的整数。")
            return

        self.run_button.state(["disabled"])
        self.batch_button.state(["disabled"])
        self.save_button.state(["disabled"])
        self.status_var_batch.set(f"批量运行中（共 {len(pairs)} 条）...")
        self.progress.configure(value=0, maximum=len(pairs))
        self._write_result("批量任务启动...", self.batch_result_box)

        thread = threading.Thread(
            target=self._batch_worker, args=(pairs, timeout, concurrency), daemon=True
        )
        thread.start()

    def _batch_worker(self, pairs, timeout: int, concurrency: int):
        def progress_cb(done, total):
            self.root.after(0, self.update_progress, done, total)

        try:
            results = trio.run(run_batch_lookup, pairs, timeout, concurrency, progress_cb)
            formatted_lines = [format_batch_result(email, password, result) for email, password, result in results]
            formatted = "\n\n".join(formatted_lines)
            self.root.after(0, self._on_batch_result, results, formatted)
        except Exception as exc:
            self.root.after(0, self._on_error, exc)

    def update_progress(self, done: int, total: int):
        self.progress.configure(value=done, maximum=total)
        self.status_var_batch.set(f"已处理 {done}/{total}")

    def _on_batch_result(self, results, text: str):
        self.last_batch_results = results
        self._write_result(text, self.batch_result_box)
        self.status_var_batch.set("批量完成")
        self.run_button.state(["!disabled"])
        self.batch_button.state(["!disabled"])
        if results:
            self.save_button.state(["!disabled"])

    def _parse_pairs(self, path: str):
        pairs = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or ":" not in line:
                        continue
                    email, password = line.split(":", 1)
                    email = email.strip()
                    password = password.strip()
                    if not email or not password or not is_email(email):
                        continue
                    pairs.append((email, password))
        except Exception as exc:
            messagebox.showerror("文件错误", str(exc))
            return []
        return pairs

    def save_batch_excel(self):
        if not self.last_batch_results:
            messagebox.showinfo("无数据", "暂无可保存的批量结果。")
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
            title="保存批量结果",
        )
        if not path:
            return

        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "亚马逊结果"
            ws.append(
                [
                    "邮箱",
                    "密码",
                    "站点",
                    "存在",
                    "受限",
                    "找回邮箱",
                    "找回电话",
                    "其他",
                ]
            )
            for email, password, result in self.last_batch_results:
                ws.append(
                    [
                        email,
                        password,
                        result.get("domain") if result else "",
                        result.get("exists") if result else "",
                        result.get("rateLimit") if result else "",
                        result.get("emailrecovery") if result else "",
                        result.get("phoneNumber") if result else "",
                        result.get("others") if result else "",
                    ]
                )
            wb.save(path)
            messagebox.showinfo("已保存", f"结果已保存到 {path}")
        except Exception as exc:
            messagebox.showerror("保存错误", str(exc))


def main():
    root = tk.Tk()
    AmazonUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
