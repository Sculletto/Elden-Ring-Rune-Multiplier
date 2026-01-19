# -*- coding: utf-8 -*-
#!/usr/bin/env python3
from __future__ import annotations

import re
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, List, Tuple

INT_RE = re.compile(r"^[+-]?\d+$")

# Columns to multiply (must match header names exactly)
TARGET_COLUMNS = ["getSoul", "bonusSoul_single", "bonusSoul_multi"]


def normalize_drop_path(data: str) -> str:
    data = (data or "").strip()
    if not data:
        return ""
    if data.startswith("{"):
        end = data.find("}")
        first = data[1:end] if end != -1 else data.strip("{}")
    else:
        first = data.split()[0]
    return first


def split_records_with_spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    start = 0
    i = 0
    in_quotes = False
    n = len(text)

    while i < n:
        ch = text[i]
        if ch == '"':
            if in_quotes:
                if i + 1 < n and text[i + 1] == '"':
                    i += 2
                    continue
                in_quotes = False
                i += 1
                continue
            else:
                in_quotes = True
                i += 1
                continue

        if not in_quotes and ch == "\n":
            spans.append((start, i + 1))
            start = i + 1

        i += 1

    if start < n:
        spans.append((start, n))
    return spans


def split_fields_with_spans(record: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    i = 0
    n = len(record)
    in_quotes = False
    field_start = 0

    while i < n:
        ch = record[i]
        if ch == '"':
            if in_quotes:
                if i + 1 < n and record[i + 1] == '"':
                    i += 2
                    continue
                in_quotes = False
                i += 1
                continue
            else:
                in_quotes = True
                i += 1
                continue

        if ch == "," and not in_quotes:
            spans.append((field_start, i))
            field_start = i + 1

        i += 1

    spans.append((field_start, n))
    return spans


def unquote_csv_field(raw: str) -> Tuple[str, bool]:
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        inner = raw[1:-1].replace('""', '"')
        return inner, True
    return raw, False


def rewrap_csv_field(new_inner: str, was_quoted: bool) -> str:
    if was_quoted:
        escaped = new_inner.replace('"', '""')
        return '"' + escaped + '"'
    return new_inner


def parse_multiplier(s: str) -> Decimal:
    s = (s or "").strip()
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError("Multiplier must be a number.")
    if d < Decimal("0") or d > Decimal("10"):
        raise ValueError("Multiplier must be between 0.00 and 10.00.")
    return d


def multiply_int_round_half_up(old_int: int, multiplier: Decimal) -> int:
    val = Decimal(old_int) * multiplier
    rounded = val.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(rounded)


def read_text_preserve_newlines(path: Path) -> str:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


def write_text_preserve_newlines(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def find_target_column_indices(header_line: str) -> Dict[str, int]:
    spans = split_fields_with_spans(header_line)
    fields = [header_line[a:b] for (a, b) in spans]

    name_to_index: Dict[str, int] = {}
    for idx, raw in enumerate(fields):
        inner, _q = unquote_csv_field(raw)
        if inner in TARGET_COLUMNS:
            name_to_index[inner] = idx
    return name_to_index


def edit_targets_in_csv_text(text: str, multiplier: Decimal) -> Tuple[str, int, int]:
    """
    Returns (edited_text, changed_count, skipped_zero_count)
    """
    rec_spans = split_records_with_spans(text)
    if not rec_spans:
        raise ValueError("No records found.")

    # Header record
    hs, he = rec_spans[0]
    header_raw = text[hs:he]
    header_line = header_raw.rstrip("\r\n")

    col_map = find_target_column_indices(header_line)
    if not col_map:
        raise ValueError("None of the target columns were found in the header.")

    missing = [c for c in TARGET_COLUMNS if c not in col_map]
    # Not fatal: we'll just ignore missing columns
    # (You can make this fatal if you want.)

    # Collect edits as (start, end, replacement)
    edits: List[Tuple[int, int, str]] = []
    changed = 0
    skipped_zero = 0

    # Process each data row
    for r_i, (rs, re_) in enumerate(rec_spans):
        if r_i == 0:
            continue  # header unchanged

        record_with_nl = text[rs:re_]
        if record_with_nl.endswith("\r\n"):
            record_body = record_with_nl[:-2]
        elif record_with_nl.endswith("\n"):
            record_body = record_with_nl[:-1]
        else:
            record_body = record_with_nl

        field_spans = split_fields_with_spans(record_body)

        for col_name, col_index in col_map.items():
            if col_index >= len(field_spans):
                continue

            fa, fb = field_spans[col_index]
            raw_field = record_body[fa:fb]

            inner, was_quoted = unquote_csv_field(raw_field)

            # Preserve inner leading/trailing whitespace exactly
            m = re.match(r"^(\s*)(.*?)(\s*)$", inner, flags=re.DOTALL)
            lead = m.group(1)
            core = m.group(2)
            trail = m.group(3)

            if not INT_RE.fullmatch(core or ""):
                continue

            old_val = int(core)
            if old_val == 0:
                skipped_zero += 1
                continue  # ignore zeros completely

            new_val = multiply_int_round_half_up(old_val, multiplier)

            new_inner = lead + str(new_val) + trail
            new_raw_field = rewrap_csv_field(new_inner, was_quoted)

            global_field_start = rs + fa
            global_field_end = rs + fb

            # Only add edit if it actually changes text
            if new_raw_field != text[global_field_start:global_field_end]:
                edits.append((global_field_start, global_field_end, new_raw_field))
                changed += 1

    # Apply edits from back to front so indices remain valid
    edits.sort(key=lambda x: x[0], reverse=True)
    out = text
    for start, end, rep in edits:
        out = out[:start] + rep + out[end:]

    return out, changed, skipped_zero


def process_file(path: str, multiplier: Decimal, status_label: tk.Label) -> None:
    p = Path(path)
    if not p.exists():
        messagebox.showerror("File not found", "Could not find:\n" + path)
        return

    status_label.config(text="Reading: " + p.name)
    status_label.update_idletasks()

    try:
        raw = read_text_preserve_newlines(p)
    except Exception:
        with open(p, "r", encoding="utf-8", errors="replace", newline="") as f:
            raw = f.read()

    try:
        edited, changed, skipped_zero = edit_targets_in_csv_text(raw, multiplier)
    except Exception as e:
        messagebox.showerror("Edit failed", str(e))
        status_label.config(text="Drop a CSV here")
        return

    mult_tag = str(multiplier).replace(".", "_")
    out_path = p.with_name(p.stem + "_soulx" + mult_tag + p.suffix)

    try:
        write_text_preserve_newlines(out_path, edited)
    except Exception as e:
        messagebox.showerror("Write failed", "Could not write output:\n\n" + str(e))
        status_label.config(text="Drop a CSV here")
        return

    msg = (
        "Wrote:\n" + str(out_path) + "\n\n"
        "Columns: " + ", ".join([c for c in TARGET_COLUMNS if c in edited]) + "\n"
        "Cells changed: " + str(changed) + "\n"
        "Zero cells skipped: " + str(skipped_zero)
    )
    messagebox.showinfo("Done", msg)
    status_label.config(text="Drop a CSV here")


def main() -> None:
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
    except ImportError:
        raise SystemExit("Missing dependency: tkinterdnd2\nInstall with: python -m pip install tkinterdnd2")

    root = TkinterDnD.Tk()
    root.title("Elden Ring Param - Soul Mass Editor")
    root.geometry("580x340")
    root.resizable(False, False)

    frame = tk.Frame(root, padx=16, pady=16)
    frame.pack(fill="both", expand=True)

    title = tk.Label(frame, text="Multiply soul fields for ALL rows", font=("Arial", 16, "bold"))
    title.pack(pady=(8, 6))

    sub = tk.Label(
        frame,
        text="Targets: getSoul, bonusSoul_single, bonusSoul_multi\n"
             "Formatting is preserved. Only integer digits in those cells are changed.\n"
             "Zeros are ignored (left untouched).",
        font=("Arial", 10),
        justify="center",
    )
    sub.pack(pady=(0, 14))

    row = tk.Frame(frame)
    row.pack(pady=(0, 10))

    tk.Label(row, text="Multiplier (0.00-10.00):", font=("Arial", 11)).pack(side="left", padx=(0, 8))
    mult_var = tk.StringVar(value="1.00")
    entry = tk.Entry(row, textvariable=mult_var, width=10, font=("Arial", 11))
    entry.pack(side="left")

    drop_zone = tk.Label(
        frame,
        text="Drag and Drop CSV Here",
        font=("Arial", 14),
        relief="groove",
        bd=2,
        width=40,
        height=4,
    )
    drop_zone.pack(pady=10)

    status = tk.Label(frame, text="Drop a CSV here", font=("Arial", 10))
    status.pack(pady=(8, 0))

    def on_drop(event) -> None:
        path = normalize_drop_path(event.data)
        if not path:
            return
        try:
            m = parse_multiplier(mult_var.get())
        except Exception as e:
            messagebox.showerror("Bad multiplier", str(e))
            return
        process_file(path, m, status)

    drop_zone.drop_target_register(DND_FILES)
    drop_zone.dnd_bind("<<Drop>>", on_drop)

    root.mainloop()


if __name__ == "__main__":
    main()
