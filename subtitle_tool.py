# -*- coding: utf-8 -*-
"""
字幕调整器 v1.0 — SRT 字幕时间轴偏移与转换工具
=================================================
痛点：SRT 字幕时间轴整体偏移、FPS 转换时轴错位、多个 SRT 需要合并。

功能：
  - 打开 SRT 文件，在文本区完整展示内容
  - 时间偏移：+3.5 秒 或 -1.2 秒，实时预览第一条字幕变化
  - 执行偏移后保存为新文件（原名_shifted.srt）
  - FPS 转换：25fps ↔ 24fps 等，自动重新计算时间戳
  - SRT 合并：合并两个 SRT 文件（时间轴不重叠则原样，重叠则累计偏移）

依赖：仅使用 Python 标准库（re / datetime / tkinter）。
"""

import os
import re
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
from pathlib import Path
from datetime import timedelta

# ============================================================================
# 配色与常量
# ============================================================================
COLOR_BG = "#1e1e2e"
COLOR_CARD = "#2a2a3c"
COLOR_ACCENT = "#6366f1"          # 靛色强调
COLOR_ACCENT_HOVER = "#4f46e5"
COLOR_TEXT = "#e0e0e0"
COLOR_TEXT_SECONDARY = "#a0a0b0"
COLOR_ENTRY_BG = "#3a3a4c"
COLOR_GREEN = "#4ade80"
COLOR_WARN = "#fbbf24"


# ============================================================================
# SRT 时间码工具函数
# ============================================================================

SRT_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,\.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,\.](\d{3})"
)


def time_to_ms(h: int, m: int, s: int, ms: int) -> int:
    """时间码转为总毫秒数"""
    return ((h * 3600 + m * 60 + s) * 1000 + ms)


def ms_to_time(total_ms: int) -> str:
    """总毫秒数转为 SRT 时间码字符串 HH:MM:SS,mmm（向下取整）"""
    total_ms = max(0, total_ms)
    h = total_ms // 3600000
    m = (total_ms % 3600000) // 60000
    s = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(text: str) -> list[dict]:
    """
    解析 SRT 文本，返回字幕块列表。
    每个块：{"index": int, "start_ms": int, "end_ms": int, "text": str, "raw_time": str}
    """
    blocks = []
    # 按空行分割
    raw_blocks = re.split(r"\n\s*\n", text.strip())
    for blk in raw_blocks:
        blk = blk.strip()
        if not blk:
            continue
        lines = blk.split("\n")
        if len(lines) < 3:
            continue
        idx_str = lines[0].strip()
        try:
            idx = int(idx_str)
        except ValueError:
            continue

        time_line = lines[1].strip()
        match = SRT_TIME_RE.search(time_line)
        if not match:
            continue

        start_ms = time_to_ms(int(match.group(1)), int(match.group(2)),
                               int(match.group(3)), int(match.group(4)))
        end_ms = time_to_ms(int(match.group(5)), int(match.group(6)),
                             int(match.group(7)), int(match.group(8)))
        text_body = "\n".join(lines[2:]).strip()

        blocks.append({
            "index": idx,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": text_body,
            "raw_time": time_line,
        })

    return blocks


def srt_to_text(blocks: list[dict], reindex: bool = True) -> str:
    """将字幕块列表序列化为 SRT 文本"""
    lines = []
    for i, blk in enumerate(blocks):
        if reindex:
            block_idx = i + 1
        else:
            block_idx = blk["index"]
        start_str = ms_to_time(blk["start_ms"])
        end_str = ms_to_time(blk["end_ms"])
        lines.append(str(block_idx))
        lines.append(f"{start_str} --> {end_str}")
        lines.append(blk["text"])
        lines.append("")  # 空行分隔
    return "\n".join(lines)


def shift_blocks(blocks: list[dict], offset_seconds: float) -> list[dict]:
    """对所有字幕块应用时间偏移（秒，正=后移/负=前移）"""
    offset_ms = int(offset_seconds * 1000)
    result = []
    for blk in blocks:
        new_blk = blk.copy()
        new_blk["start_ms"] = max(0, blk["start_ms"] + offset_ms)
        new_blk["end_ms"] = max(0, blk["end_ms"] + offset_ms)
        result.append(new_blk)
    return result


def fps_convert_blocks(blocks: list[dict], src_fps: float, dst_fps: float) -> list[dict]:
    """FPS 转换：重新按比例计算时间戳"""
    ratio = dst_fps / src_fps
    result = []
    for blk in blocks:
        new_blk = blk.copy()
        new_blk["start_ms"] = int(blk["start_ms"] * ratio + 0.5)
        new_blk["end_ms"] = int(blk["end_ms"] * ratio + 0.5)
        result.append(new_blk)
    return result


def merge_srt_blocks(a: list[dict], b: list[dict]) -> list[dict]:
    """合并两个 SRT 字幕块列表 — B 叠加在 A 之后，时间偏移到最后"""
    if not a:
        return [blk.copy() for blk in b]
    if not b:
        return [blk.copy() for blk in a]

    last_end = a[-1]["end_ms"]
    merged = [blk.copy() for blk in a]

    for blk in b:
        new_blk = blk.copy()
        dur = blk["end_ms"] - blk["start_ms"]
        new_blk["start_ms"] = last_end + 1000  # 1秒间隔
        new_blk["end_ms"] = new_blk["start_ms"] + dur
        last_end = new_blk["end_ms"]
        merged.append(new_blk)

    return merged


# ============================================================================
# 主程序
# ============================================================================

class SubtitleToolApp:
    """字幕调整器 v1.0"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("字幕调整器 v1.0")
        self.root.geometry("520x480")
        self.root.resizable(False, False)
        self.root.configure(bg=COLOR_BG)

        self.current_blocks: list[dict] = []
        self.current_file: str | None = None
        self.merge_blocks: list[dict] = []   # 待合并的另一个 SRT
        self.merge_file: str | None = None

        self._setup_styles()
        self._build_ui()

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=COLOR_BG, foreground=COLOR_TEXT, font=("微软雅黑", 9))
        style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT)
        style.configure("TEntry", fieldbackground=COLOR_ENTRY_BG, foreground=COLOR_TEXT, insertcolor=COLOR_TEXT)
        style.configure("Card.TLabelframe", background=COLOR_CARD, foreground=COLOR_ACCENT)
        style.configure("Card.TLabelframe.Label", background=COLOR_CARD, foreground=COLOR_ACCENT,
                        font=("微软雅黑", 10, "bold"))
        style.configure("Accent.TButton", background=COLOR_ACCENT, foreground="#ffffff",
                        borderwidth=0, font=("微软雅黑", 9, "bold"))
        style.map("Accent.TButton", background=[("active", COLOR_ACCENT_HOVER)])
        style.configure("Secondary.TButton", background="#444466", foreground=COLOR_TEXT, borderwidth=0)
        style.map("Secondary.TButton", background=[("active", "#555577")])

    def _build_ui(self):
        pad = {"padx": 8, "pady": 2}

        # ---- 文件操作行 ----
        frm_file = ttk.LabelFrame(self.root, text="SRT 文件", style="Card.TLabelframe")
        frm_file.pack(fill=tk.X, **pad, pady=(8, 4))

        row = tk.Frame(frm_file, bg=COLOR_CARD)
        row.pack(fill=tk.X, padx=6, pady=4)
        ttk.Button(row, text="打开 SRT", command=self._on_open,
                   style="Accent.TButton").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row, text="合并 SRT", command=self._on_load_merge,
                   style="Secondary.TButton").pack(side=tk.LEFT)
        self.lbl_file = tk.Label(row, text="未打开文件", bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY,
                                  font=("微软雅黑", 8))
        self.lbl_file.pack(side=tk.RIGHT)

        # ---- 偏移设置行 ----
        frm_shift = ttk.LabelFrame(self.root, text="时间偏移", style="Card.TLabelframe")
        frm_shift.pack(fill=tk.X, **pad)

        inner = tk.Frame(frm_shift, bg=COLOR_CARD)
        inner.pack(fill=tk.X, padx=6, pady=4)

        tk.Label(inner, text="偏移量（秒）：", bg=COLOR_CARD, fg=COLOR_TEXT).pack(side=tk.LEFT)
        self.offset_entry = tk.Entry(inner, bg=COLOR_ENTRY_BG, fg=COLOR_TEXT,
                                      insertbackground=COLOR_TEXT, width=8,
                                      font=("Consolas", 10))
        self.offset_entry.insert(0, "0.0")
        self.offset_entry.pack(side=tk.LEFT, padx=(4, 8))

        ttk.Button(inner, text="预览偏移", command=self._on_preview,
                   style="Accent.TButton").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(inner, text="应用偏移", command=self._on_apply_shift,
                   style="Secondary.TButton").pack(side=tk.LEFT)

        self.lbl_preview = tk.Label(inner, text="", bg=COLOR_CARD, fg=COLOR_GREEN,
                                     font=("Consolas", 8))
        self.lbl_preview.pack(side=tk.LEFT, padx=(8, 0))

        # ---- FPS 转换行 ----
        frm_fps = ttk.LabelFrame(self.root, text="FPS 转换", style="Card.TLabelframe")
        frm_fps.pack(fill=tk.X, **pad)

        inner2 = tk.Frame(frm_fps, bg=COLOR_CARD)
        inner2.pack(fill=tk.X, padx=6, pady=4)

        tk.Label(inner2, text="源帧率：", bg=COLOR_CARD, fg=COLOR_TEXT).pack(side=tk.LEFT)
        self.fps_src_var = tk.StringVar(value="25")
        fps_vals = ["23.976", "24", "25", "29.97", "30", "50", "60"]
        ttk.Combobox(inner2, textvariable=self.fps_src_var, values=fps_vals,
                    state="readonly", width=6, font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=(2, 8))

        tk.Label(inner2, text="目标帧率：", bg=COLOR_CARD, fg=COLOR_TEXT).pack(side=tk.LEFT)
        self.fps_dst_var = tk.StringVar(value="24")
        ttk.Combobox(inner2, textvariable=self.fps_dst_var, values=fps_vals,
                    state="readonly", width=6, font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=(2, 8))

        ttk.Button(inner2, text="FPS 转换", command=self._on_fps_convert,
                   style="Secondary.TButton").pack(side=tk.LEFT)

        # ---- 字幕内容显示 ----
        frm_text = ttk.LabelFrame(self.root, text="字幕内容", style="Card.TLabelframe")
        frm_text.pack(fill=tk.BOTH, expand=True, **pad, pady=(4, 4))

        self.stext = scrolledtext.ScrolledText(frm_text, bg=COLOR_ENTRY_BG, fg=COLOR_TEXT,
                                                font=("Consolas", 9), wrap=tk.NONE,
                                                relief=tk.FLAT)
        self.stext.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        # ---- 操作行 ----
        frm_ops = tk.Frame(self.root, bg=COLOR_BG)
        frm_ops.pack(fill=tk.X, **pad, pady=(2, 8))

        ttk.Button(frm_ops, text="保存为新文件", command=self._on_save,
                   style="Accent.TButton").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(frm_ops, text="执行合并保存", command=self._on_merge_save,
                   style="Secondary.TButton").pack(side=tk.LEFT)

        self.lbl_status = tk.Label(frm_ops, text="", bg=COLOR_BG, fg=COLOR_TEXT_SECONDARY,
                                    font=("微软雅黑", 8))
        self.lbl_status.pack(side=tk.RIGHT)

    # ------------------------------------------------------------------
    def _on_open(self):
        file_path = filedialog.askopenfilename(
            title="打开 SRT 字幕文件",
            filetypes=[("SRT 字幕", "*.srt"), ("所有文件", "*.*")],
        )
        if not file_path:
            return
        self._load_srt(file_path)

    def _load_srt(self, file_path: str):
        try:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                text = f.read()
        except UnicodeDecodeError:
            try:
                with open(file_path, "r", encoding="gbk") as f:
                    text = f.read()
            except Exception as e:
                messagebox.showerror("错误", f"无法读取文件：{e}")
                return
        except Exception as e:
            messagebox.showerror("错误", f"无法读取文件：{e}")
            return

        self.current_file = file_path
        self.current_blocks = parse_srt(text)
        self.lbl_file.config(text=os.path.basename(file_path), fg=COLOR_GREEN)
        self.lbl_status.config(text=f"已加载 {len(self.current_blocks)} 条字幕")
        self._refresh_display()

    def _refresh_display(self):
        """刷新字幕文本显示区"""
        text = srt_to_text(self.current_blocks)
        self.stext.delete("1.0", tk.END)
        self.stext.insert("1.0", text)

    # ------------------------------------------------------------------
    def _on_preview(self):
        """预览偏移效果（显示第一条字幕的变化）"""
        if not self.current_blocks:
            self.lbl_preview.config(text="请先打开 SRT 文件", fg=COLOR_WARN)
            return
        try:
            offset = float(self.offset_entry.get().strip())
        except ValueError:
            self.lbl_preview.config(text="偏移量无效", fg=COLOR_WARN)
            return

        shifted = shift_blocks(self.current_blocks, offset)
        first = shifted[0]
        self.lbl_preview.config(
            text=f"第1条: {ms_to_time(first['start_ms'])} --> {ms_to_time(first['end_ms'])}",
            fg=COLOR_GREEN,
        )

    # ------------------------------------------------------------------
    def _on_apply_shift(self):
        """确认应用偏移"""
        if not self.current_blocks:
            messagebox.showinfo("提示", "请先打开 SRT 文件")
            return
        try:
            offset = float(self.offset_entry.get().strip())
        except ValueError:
            messagebox.showerror("错误", "请输入有效的偏移量（例如 3.5 或 -1.2）")
            return

        self.current_blocks = shift_blocks(self.current_blocks, offset)
        self._refresh_display()
        self.lbl_status.config(text=f"已应用偏移 {offset:+.1f} 秒")
        self.lbl_preview.config(text=f"偏移 {offset:+.1f}s 已应用", fg=COLOR_GREEN)

    # ------------------------------------------------------------------
    def _on_fps_convert(self):
        if not self.current_blocks:
            messagebox.showinfo("提示", "请先打开 SRT 文件")
            return
        try:
            src = float(self.fps_src_var.get())
            dst = float(self.fps_dst_var.get())
        except ValueError:
            return
        if src == dst:
            messagebox.showinfo("提示", "源帧率和目标帧率相同，无需转换")
            return
        if src <= 0 or dst <= 0:
            return

        self.current_blocks = fps_convert_blocks(self.current_blocks, src, dst)
        self._refresh_display()
        self.lbl_status.config(text=f"FPS 转换完成：{src:.3f} → {dst:.3f}")
        self.lbl_preview.config(text=f"{src:.3f}→{dst:.3f} 已转换", fg=COLOR_GREEN)

    # ------------------------------------------------------------------
    def _on_load_merge(self):
        """加载待合并的第二个 SRT"""
        file_path = filedialog.askopenfilename(
            title="选择要合并的第二个 SRT 文件",
            filetypes=[("SRT 字幕", "*.srt"), ("所有文件", "*.*")],
        )
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                text = f.read()
        except UnicodeDecodeError:
            try:
                with open(file_path, "r", encoding="gbk") as f:
                    text = f.read()
            except Exception as e:
                messagebox.showerror("错误", f"无法读取文件：{e}")
                return
        except Exception as e:
            messagebox.showerror("错误", f"无法读取文件：{e}")
            return

        self.merge_file = file_path
        self.merge_blocks = parse_srt(text)
        self.lbl_status.config(
            text=f"待合并：{os.path.basename(file_path)} ({len(self.merge_blocks)} 条)"
        )

    def _on_merge_save(self):
        if not self.current_blocks:
            messagebox.showinfo("提示", "请先打开主 SRT 文件")
            return
        if not self.merge_blocks:
            messagebox.showinfo("提示", "请先加载要合并的第二个 SRT")
            return

        merged = merge_srt_blocks(self.current_blocks, self.merge_blocks)
        # 另存为
        if self.current_file:
            base = Path(self.current_file)
            save_path = filedialog.asksaveasfilename(
                title="保存合并后的字幕",
                initialfile=f"{base.stem}_merged.srt",
                defaultextension=".srt",
                filetypes=[("SRT 字幕", "*.srt")],
            )
            if not save_path:
                return
        else:
            return

        try:
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(srt_to_text(merged))
            messagebox.showinfo("保存成功", f"合并字幕已保存到：\n{save_path}")
            self.lbl_status.config(text=f"已保存合并：{os.path.basename(save_path)} ({len(merged)} 条)")
        except Exception as e:
            messagebox.showerror("错误", f"保存失败：{e}")

    # ------------------------------------------------------------------
    def _on_save(self):
        """另存为 .srt"""
        if not self.current_blocks:
            messagebox.showinfo("提示", "请先打开或修改 SRT 文件")
            return
        if self.current_file:
            base = Path(self.current_file)
            initial = f"{base.stem}_shifted.srt"
        else:
            initial = "output.srt"

        save_path = filedialog.asksaveasfilename(
            title="保存调整后的字幕",
            initialfile=initial,
            defaultextension=".srt",
            filetypes=[("SRT 字幕", "*.srt")],
        )
        if not save_path:
            return
        try:
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(srt_to_text(self.current_blocks))
            messagebox.showinfo("保存成功", f"字幕已保存到：\n{save_path}")
            self.lbl_status.config(text=f"已保存：{os.path.basename(save_path)}")
        except Exception as e:
            messagebox.showerror("错误", f"保存失败：{e}")


# ============================================================================
# 入口
# ============================================================================

def main():
    root = tk.Tk()
    try:
        root.iconbitmap(default="")
    except Exception:
        pass
    SubtitleToolApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
