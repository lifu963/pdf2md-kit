"""
build.pipeline — 纯函数 Markdown 合并与清理管道

所有函数满足以下约束：
- 无文件系统依赖（不导入 pathlib / os / glob 等）
- 无时钟依赖（不导入 datetime / time）
- 无副作用：固定输入产生固定输出，函数幂等
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── 正则 ────────────────────────────────────────────────────────────────────

PAGE_MARKER_RE = re.compile(r"^\s*\*\*第\s*\d+\s*页\*\*\s*$")
PAGE_SPLIT_RE  = re.compile(r"^\s*---\s*$")
HEADING_RE     = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
IMAGE_REF_RE   = re.compile(r"^!\[.*?\]\(.*?\)\s*$")
FAILED_PAGE_RE = re.compile(r"^<!--\s*提取失败")
EMPTY_PAGE_RE  = re.compile(r"^\*（本页无内容）\*\s*$")

_HEADING_STRIP_TRANS = str.maketrans(
    "", "",
    " \t\n\r\u2014\u2013_\u00b7\u2022\u201c\u201d\"\u2018\u2019'`\uff1a:\uff08\uff09()[]，、。！？!?,.",
)
DEDUP_NORM_RE = re.compile(
    r"[*\s?？!！。.，,、；;：:\u201c\u201d\u2018\u2019\"'()（）\[\]【】]"
)

# ── 统计 ────────────────────────────────────────────────────────────────────


@dataclass
class CleanStats:
    original_lines: int = 0
    output_lines: int = 0
    removed_page_markers: int = 0
    removed_page_splits: int = 0
    removed_image_refs: int = 0
    removed_failed_pages: int = 0
    removed_duplicate_headings: int = 0
    removed_empty_sections: int = 0
    removed_duplicate_fragments: int = 0
    removed_empty_pages: int = 0


# ── 合并 ────────────────────────────────────────────────────────────────────


def merge_pages(page_contents: list[str], title: str) -> list[str]:
    """将各页正文字符串合并为行列表（纯函数，不读取文件）。

    参数：
        page_contents: 每页正文文本（与页码顺序一致）。
        title:         文档标题，生成为首行 H1。
    """
    lines: list[str] = [f"# {title}", ""]
    for i, content in enumerate(page_contents, start=1):
        stripped = content.strip()
        lines.append("---")
        lines.append("")
        lines.append(f"**第 {i} 页**")
        lines.append("")
        if stripped:
            lines.extend(stripped.splitlines())
            lines.append("")
        else:
            lines.append("*（本页无内容）*")
            lines.append("")
    return lines


# ── 清理辅助 ─────────────────────────────────────────────────────────────────


def _normalize_for_dedup(text: str) -> str:
    return DEDUP_NORM_RE.sub("", text)


def _normalize_heading_for_dedup(text: str) -> str:
    t = text.replace("**", "").strip()
    t = t.replace("【", "").replace("】", "")
    t = t.translate(_HEADING_STRIP_TRANS)
    return t


# ── 清理函数 ─────────────────────────────────────────────────────────────────


def strip_page_scaffold(lines: list[str], stats: CleanStats) -> list[str]:
    """删除页码标记、分页分隔线、图片引用、失败页注释及空页占位符。"""
    page_marker_set: set[int] = {
        i for i, ln in enumerate(lines) if PAGE_MARKER_RE.match(ln)
    }

    def _is_scaffold_split(idx: int) -> bool:
        if not PAGE_SPLIT_RE.match(lines[idx]):
            return False
        for j in range(idx + 1, min(idx + 5, len(lines))):
            if lines[j].strip():
                return j in page_marker_set
        return False

    out: list[str] = []
    for i, line in enumerate(lines):
        if PAGE_MARKER_RE.match(line):
            stats.removed_page_markers += 1
        elif _is_scaffold_split(i):
            stats.removed_page_splits += 1
        elif IMAGE_REF_RE.match(line):
            stats.removed_image_refs += 1
        elif FAILED_PAGE_RE.match(line):
            stats.removed_failed_pages += 1
        elif EMPTY_PAGE_RE.match(line):
            stats.removed_empty_pages += 1
        else:
            out.append(line)
    return out


def normalize_heading_levels(lines: list[str], max_heading_level: int) -> list[str]:
    """首个标题 → H1；其余保持相对层级，最小层级映射到 H2。"""
    min_level: int | None = None
    found_first = False
    for line in lines:
        m = HEADING_RE.match(line)
        if not m:
            continue
        if not found_first:
            found_first = True
            continue
        level = len(m.group(1))
        if level > 1 and (min_level is None or level < min_level):
            min_level = level
    if min_level is None:
        min_level = 2

    out: list[str] = []
    title_done = False
    for line in lines:
        m = HEADING_RE.match(line)
        if not m:
            out.append(line)
            continue
        raw_text = m.group(2).rstrip()
        old_level = len(m.group(1))
        if not title_done:
            out.append(f"# {raw_text}")
            title_done = True
            continue
        if old_level == 1:
            new_level = 2
        else:
            new_level = max(2, min(2 + old_level - min_level, max_heading_level))
        out.append(f"{'#' * new_level} {raw_text}")
    return out


def merge_duplicate_headings(lines: list[str], stats: CleanStats) -> list[str]:
    """删除分页导致的重复标题行（保留正文）。"""
    out: list[str] = []
    current_path: dict[int, str] = {}
    seen_fingerprints: set[tuple] = set()

    for line in lines:
        m = HEADING_RE.match(line)
        if not m:
            out.append(line)
            continue
        level = len(m.group(1))
        norm = _normalize_heading_for_dedup(m.group(2).strip())
        for key in list(current_path.keys()):
            if key > level:
                del current_path[key]
        parent_path = tuple(current_path.get(l, "") for l in range(1, level))
        fingerprint = (level, norm, parent_path)
        if norm and fingerprint in seen_fingerprints:
            stats.removed_duplicate_headings += 1
            continue
        seen_fingerprints.add(fingerprint)
        current_path[level] = norm
        out.append(line)
    return out


def remove_empty_sections(lines: list[str], stats: CleanStats) -> list[str]:
    """删除无实质内容的 H2+ 标题段落。"""
    heading_info: list[tuple[int, int]] = [
        (i, len(m.group(1)))
        for i, line in enumerate(lines)
        if (m := HEADING_RE.match(line))
    ]
    headings_to_remove: set[int] = set()

    for idx, (line_idx, level) in enumerate(heading_info):
        if level <= 1:
            continue
        if idx + 1 < len(heading_info):
            next_line_idx, next_level = heading_info[idx + 1]
        else:
            next_line_idx, next_level = len(lines), 0
        if next_level > level:
            continue
        if not any(lines[i].strip() for i in range(line_idx + 1, next_line_idx)):
            headings_to_remove.add(line_idx)

    stats.removed_empty_sections += len(headings_to_remove)
    return [line for i, line in enumerate(lines) if i not in headings_to_remove]


def remove_duplicate_fragments(lines: list[str], stats: CleanStats) -> list[str]:
    """删除相邻（仅隔空行）的重复正文行。"""
    out: list[str] = []
    prev_norm: str | None = None
    blank_since_prev = False

    for line in lines:
        if HEADING_RE.match(line):
            out.append(line)
            prev_norm = None
            blank_since_prev = False
            continue
        if line.strip() == "":
            blank_since_prev = True
            out.append("")
            continue
        norm = _normalize_for_dedup(line.strip())
        if len(norm) < 4:
            out.append(line)
            continue
        if blank_since_prev and prev_norm and prev_norm.startswith("|"):
            prev_norm = None
        if norm == prev_norm and blank_since_prev:
            stats.removed_duplicate_fragments += 1
            continue
        prev_norm = norm
        blank_since_prev = False
        out.append(line)
    return out


def compact_blank_lines(lines: list[str]) -> list[str]:
    """将连续空行压缩为单空行，同时删除首尾空行。"""
    out: list[str] = []
    prev_blank = True
    for line in lines:
        is_blank = line.strip() == ""
        if is_blank and prev_blank:
            continue
        out.append("" if is_blank else line)
        prev_blank = is_blank
    while out and out[-1].strip() == "":
        out.pop()
    return out


def build_rule_based_baseline(lines: list[str], *, max_heading_level: int = 5) -> list[str]:
    """运行当前纯规则 build 清理链路，生成 LLM 参考 baseline。"""
    stats = CleanStats(original_lines=len(lines))
    cleaned = strip_page_scaffold(lines, stats)
    cleaned = normalize_heading_levels(cleaned, max_heading_level=max_heading_level)
    cleaned = merge_duplicate_headings(cleaned, stats)

    previous_len = -1
    while len(cleaned) != previous_len:
        previous_len = len(cleaned)
        cleaned = remove_empty_sections(cleaned, stats)

    cleaned = remove_duplicate_fragments(cleaned, stats)
    cleaned = compact_blank_lines(cleaned)
    stats.output_lines = len(cleaned)
    return cleaned


def split_scaffold_pages(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    """按分页脚手架拆出标题前缀与各页块，供长文分块 build 使用。"""
    marker_indices = [idx for idx, line in enumerate(lines) if PAGE_MARKER_RE.match(line)]
    if not marker_indices:
        return list(lines), []

    section_starts: list[int] = []
    for idx in marker_indices:
        start = idx
        if idx >= 2 and PAGE_SPLIT_RE.match(lines[idx - 2]) and lines[idx - 1].strip() == "":
            start = idx - 2
        elif idx >= 1 and lines[idx - 1].strip() == "":
            start = idx - 1
        section_starts.append(start)

    title_lines = list(lines[: section_starts[0]])
    page_sections: list[list[str]] = []
    for position, start in enumerate(section_starts):
        end = section_starts[position + 1] if position + 1 < len(section_starts) else len(lines)
        page_sections.append(list(lines[start:end]))
    return title_lines, page_sections


def strip_wrapped_markdown_fence(text: str) -> str:
    """移除模型偶尔附带的最外层 Markdown 代码块包装。"""
    stripped = text.strip()
    if not stripped:
        return ""

    fence_lines = stripped.splitlines()
    if (
        len(fence_lines) >= 2
        and fence_lines[0].lstrip().startswith("```")
        and fence_lines[-1].strip() == "```"
    ):
        return "\n".join(fence_lines[1:-1]).strip()
    return stripped


def validate_candidate_output(source_lines: list[str], candidate_lines: list[str]) -> list[str]:
    """校验候选输出没有改写/补写/重排原始分页内容。"""
    source_material = strip_page_scaffold(source_lines, CleanStats())
    source_tokens = _tokenize_for_validation(source_material)
    candidate_tokens = _tokenize_for_validation(candidate_lines)
    if not candidate_tokens:
        return ["output is empty after normalization"]

    if source_tokens and candidate_tokens[0] != source_tokens[0]:
        return [f"output must keep the original opening line: {_token_preview(source_tokens[0])}"]

    source_index = 0
    source_token_set = set(source_tokens)
    for token in candidate_tokens:
        matched = False
        while source_index < len(source_tokens):
            if source_tokens[source_index] == token:
                matched = True
                source_index += 1
                break
            source_index += 1

        if matched:
            continue

        preview = _token_preview(token)
        if token not in source_token_set:
            return [f"output introduces new or rewritten content: {preview}"]
        return [f"output reorders source content: {preview}"]

    return []


def _tokenize_for_validation(lines: list[str]) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    normalized_lines = compact_blank_lines([line.rstrip() for line in lines])
    for line in normalized_lines:
        if not line.strip():
            continue
        heading_match = HEADING_RE.match(line)
        if heading_match:
            tokens.append(("heading", heading_match.group(2).strip()))
        else:
            tokens.append(("text", line.rstrip()))
    return tokens


def _token_preview(token: tuple[str, str]) -> str:
    kind, value = token
    preview = value if len(value) <= 80 else value[:77] + "..."
    return f"{kind}: {preview}"
