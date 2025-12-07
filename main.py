import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import httpx
import trio

from holehe.core import is_email
from holehe.modules.shopping.amazon import amazon


async def run_amazon_lookup(email: str, timeout: int):
    """Run the Amazon lookup and return the first result dict."""
    out = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        await amazon(email, client, out)
    return out[0] if out else None

async def run_batch_lookup(pairs, timeout: int, progress_callback=None):
    """Run multiple Amazon lookups sequentially, sharing one client."""
    results = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for idx, (email, password) in enumerate(pairs, start=1):
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
            results.append((email, password, result))
            if progress_callback:
                progress_callback(idx, len(pairs))
    return results


def format_result(email: str, result: dict) -> str:
    if result is None:
        return f"{email}\nStatus: no response received."

    if result.get("rateLimit"):
        return f"{email}\nStatus: rate limited or request failed for {result.get('domain', 'amazon.com')}."

    exists = result.get("exists")
    status = "Account exists" if exists else "No account found"
    lines = [
        f"Email: {email}",
        f"Domain: {result.get('domain', 'amazon.com')}",
        f"Status: {status}",
    ]

    if result.get("emailrecovery"):
        lines.append(f"Recovery email: {result['emailrecovery']}")
    if result.get("phoneNumber"):
        lines.append(f"Recovery phone: {result['phoneNumber']}")
    if result.get("others"):
        lines.append(f"Other: {result['others']}")

    return "\n".join(lines)

def format_batch_result(email: str, password: str, result: dict) -> str:
    header = f"{email}:{password}"
    formatted = format_result(email, result)
    return f"{header}\n{formatted.splitlines()[-1]}"


class AmazonUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Holehe AWS/Amazon Checker")
        self.root.resizable(False, False)

        self.email_var = tk.StringVar()
        self.timeout_var = tk.StringVar(value="10")
        self.file_path = tk.StringVar(value="No file selected")

        self._build_layout()

    def _build_layout(self):
        padding = {"padx": 12, "pady": 8}

        main_frame = ttk.Frame(self.root)
        main_frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(main_frame, text="Target email").grid(row=0, column=0, sticky="w", **padding)
        email_entry = ttk.Entry(main_frame, textvariable=self.email_var, width=32)
        email_entry.grid(row=0, column=1, sticky="ew", **padding)
        email_entry.focus()

        ttk.Label(main_frame, text="Timeout (s)").grid(row=1, column=0, sticky="w", **padding)
        timeout_entry = ttk.Entry(main_frame, textvariable=self.timeout_var, width=8)
        timeout_entry.grid(row=1, column=1, sticky="w", **padding)

        self.status_var = tk.StringVar(value="Ready.")
        self.result_box = tk.Text(main_frame, width=60, height=10, state="disabled")
        self.result_box.grid(row=3, column=0, columnspan=2, sticky="nsew", **padding)

        self.run_button = ttk.Button(main_frame, text="Check Amazon", command=self.start_lookup)
        self.run_button.grid(row=2, column=0, columnspan=2, sticky="ew", **padding)

        file_frame = ttk.Frame(main_frame)
        file_frame.grid(row=4, column=0, columnspan=2, sticky="ew", **padding)
        ttk.Button(file_frame, text="Select batch file", command=self.select_file).grid(row=0, column=0, sticky="w")
        ttk.Label(file_frame, textvariable=self.file_path, width=45).grid(row=0, column=1, sticky="w", padx=8)

        self.batch_button = ttk.Button(main_frame, text="Run batch", command=self.start_batch)
        self.batch_button.grid(row=5, column=0, columnspan=2, sticky="ew", **padding)

        self.progress = ttk.Progressbar(main_frame, mode="determinate")
        self.progress.grid(row=6, column=0, columnspan=2, sticky="ew", **padding)

        ttk.Label(main_frame, textvariable=self.status_var).grid(row=7, column=0, columnspan=2, sticky="w", **padding)

        main_frame.columnconfigure(1, weight=1)

    def start_lookup(self):
        email = self.email_var.get().strip()
        if not email:
            messagebox.showerror("Validation error", "Please enter an email address.")
            return
        if not is_email(email):
            messagebox.showerror("Validation error", "Please enter a valid email address.")
            return

        try:
            timeout = int(self.timeout_var.get())
        except ValueError:
            messagebox.showerror("Validation error", "Timeout must be an integer (seconds).")
            return

        self.run_button.state(["disabled"])
        self.status_var.set("Checking Amazon...")
        self._write_result("Running lookup...")

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
        self._write_result(formatted)
        self.status_var.set("Done.")
        self.run_button.state(["!disabled"])

    def _on_error(self, exc: Exception):
        messagebox.showerror("Lookup error", str(exc))
        self.status_var.set("Error.")
        self.run_button.state(["!disabled"])

    def _write_result(self, text: str):
        self.result_box.configure(state="normal")
        self.result_box.delete("1.0", tk.END)
        self.result_box.insert(tk.END, text)
        self.result_box.configure(state="disabled")

    def select_file(self):
        path = filedialog.askopenfilename(
            title="Select txt file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.file_path.set(path)

    def start_batch(self):
        path = self.file_path.get()
        if not path or path == "No file selected":
            messagebox.showerror("Validation error", "Please select a txt file.")
            return

        pairs = self._parse_pairs(path)
        if not pairs:
            messagebox.showerror("Validation error", "No valid lines found (expected email:password).")
            return

        try:
            timeout = int(self.timeout_var.get())
        except ValueError:
            messagebox.showerror("Validation error", "Timeout must be an integer (seconds).")
            return

        self.run_button.state(["disabled"])
        self.batch_button.state(["disabled"])
        self.status_var.set(f"Running batch ({len(pairs)} entries)...")
        self.progress.configure(value=0, maximum=len(pairs))
        self._write_result("Batch started...")

        thread = threading.Thread(
            target=self._batch_worker, args=(pairs, timeout), daemon=True
        )
        thread.start()

    def _batch_worker(self, pairs, timeout: int):
        def progress_cb(done, total):
            self.root.after(0, self.update_progress, done, total)

        try:
            results = trio.run(run_batch_lookup, pairs, timeout, progress_cb)
            formatted_lines = [format_batch_result(email, password, result) for email, password, result in results]
            formatted = "\n\n".join(formatted_lines)
            self.root.after(0, self._on_batch_result, formatted)
        except Exception as exc:
            self.root.after(0, self._on_error, exc)

    def update_progress(self, done: int, total: int):
        self.progress.configure(value=done, maximum=total)
        self.status_var.set(f"Processed {done}/{total}")

    def _on_batch_result(self, text: str):
        self._write_result(text)
        self.status_var.set("Batch complete.")
        self.run_button.state(["!disabled"])
        self.batch_button.state(["!disabled"])

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
            messagebox.showerror("File error", str(exc))
            return []
        return pairs


def main():
    root = tk.Tk()
    AmazonUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
