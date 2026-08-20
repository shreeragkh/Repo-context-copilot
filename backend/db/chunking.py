import ast
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

CODE_ONLY_MAP = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".java": "java",
    ".go": "go", ".rb": "ruby", ".rs": "rust", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp", ".php": "php",
}

EXCLUDE_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__",
                "dist", "build", ".next", "target", "vendor", ".idea", ".mypy_cache"}

EXCLUDE_FILENAMES = {"package-lock.json", "yarn.lock", "poetry.lock"}
EXCLUDE_PATTERNS = re.compile(r"\.min\.(js|css)$|\.d\.ts$|_pb2\.py$")
DOC_EXTENSIONS = {".md": "markdown", ".rst": "restructuredtext", ".txt": "text"}
PRIORITY_DOC_FILENAMES = {"readme.md", "readme.rst", "readme.txt", "readme"}

MAX_CHUNK_CHARS = 3000

GENERIC_FUNC_PATTERNS = {
    "javascript": re.compile(r"^\s*(export\s+)?(async\s+)?function\s+(\w+)|^\s*(export\s+)?class\s+(\w+)|^\s*const\s+(\w+)\s*=\s*(async\s*)?\("),
    "typescript": re.compile(r"^\s*(export\s+)?(async\s+)?function\s+(\w+)|^\s*(export\s+)?class\s+(\w+)|^\s*const\s+(\w+)\s*=\s*(async\s*)?\("),
    "java": re.compile(r"^\s*(public|private|protected)?\s*(static\s+)?[\w<>\[\]]+\s+(\w+)\s*\("),
    "go": re.compile(r"^\s*func\s+(\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\("),
    "ruby": re.compile(r"^\s*def\s+(\w+)|^\s*class\s+(\w+)"),
    "rust": re.compile(r"^\s*(pub\s+)?fn\s+(\w+)|^\s*(pub\s+)?struct\s+(\w+)"),
    "c": re.compile(r"^\s*[\w\*]+\s+(\w+)\s*\([^;]*\)\s*\{"),
    "cpp": re.compile(r"^\s*[\w\*:<>]+\s+(\w+)\s*\([^;]*\)\s*\{"),
    "csharp": re.compile(r"^\s*(public|private|protected)?\s*(static\s+)?[\w<>\[\]]+\s+(\w+)\s*\("),
    "php": re.compile(r"^\s*function\s+(\w+)|^\s*class\s+(\w+)"),
}


@dataclass
class Chunk:
    repo: str
    file_path: str
    language: str
    symbol_type: str
    symbol_name: str
    start_line: int
    end_line: int
    content: str
    char_count: int
    chunk_id: str = ""

    def __post_init__(self):
        if not self.chunk_id:
            raw = f"{self.repo}:{self.file_path}:{self.symbol_name}:{self.start_line}-{self.end_line}"
            self.chunk_id = hashlib.sha1(raw.encode()).hexdigest()[:16]


def discover_files(repo_dir: Path, max_file_kb: int = 500) -> list[Path]:
    files = []
    for root, dirs, filenames in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn in EXCLUDE_FILENAMES or EXCLUDE_PATTERNS.search(fn):
                continue
            ext = Path(fn).suffix.lower()
            is_code = ext in CODE_ONLY_MAP
            is_doc = ext in DOC_EXTENSIONS or fn.lower() in PRIORITY_DOC_FILENAMES
            if not (is_code or is_doc):
                continue
            full = Path(root) / fn
            try:
                if full.stat().st_size > max_file_kb * 1024:
                    continue
            except OSError:
                continue
            files.append(full)
    return files


def split_oversized(chunk: Chunk, max_chars: int = MAX_CHUNK_CHARS) -> list[Chunk]:
    if chunk.char_count <= max_chars:
        return [chunk]
    lines = chunk.content.splitlines()
    out, buf, buf_start, cur_len = [], [], chunk.start_line, 0
    for i, line in enumerate(lines):
        buf.append(line)
        cur_len += len(line) + 1
        if cur_len >= max_chars:
            src = "\n".join(buf)
            out.append(Chunk(chunk.repo, chunk.file_path, chunk.language, chunk.symbol_type,
                              f"{chunk.symbol_name}_part{len(out)+1}", buf_start,
                              buf_start + len(buf) - 1, src, len(src)))
            buf, buf_start, cur_len = [], chunk.start_line + i + 1, 0
    if buf:
        src = "\n".join(buf)
        out.append(Chunk(chunk.repo, chunk.file_path, chunk.language, chunk.symbol_type,
                          f"{chunk.symbol_name}_part{len(out)+1}", buf_start,
                          buf_start + len(buf) - 1, src, len(src)))
    return out


def chunk_generic_lines(path: Path, repo_name: str, text: str, language: str, window: int = 60, overlap: int = 10) -> list[Chunk]:
    lines = text.splitlines()
    chunks = []
    i, n = 0, len(lines)
    if n == 0:
        return chunks
    while i < n:
        end = min(i + window, n)
        src = "\n".join(lines[i:end])
        if src.strip():
            chunks.append(Chunk(repo_name, str(path), language, "block",
                                 f"lines_{i+1}-{end}", i + 1, end, src, len(src)))
        if end == n:
            break
        i += window - overlap
    return chunks


def chunk_markdown_file(path: Path, repo_name: str) -> list[Chunk]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    header_pattern = re.compile(r"^#{1,3}\s+(.+)")
    starts = [i for i, line in enumerate(lines) if header_pattern.match(line)]
    if not starts:
        return chunk_generic_lines(path, repo_name, text, "markdown", window=80, overlap=10)

    chunks = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] - 1 if idx + 1 < len(starts) else len(lines) - 1
        while end > start and not lines[end].strip():
            end -= 1
        src = "\n".join(lines[start:end + 1])
        name = header_pattern.match(lines[start]).group(1).strip()
        chunks.append(Chunk(repo_name, str(path), "markdown", "doc_section",
                             name, start + 1, end + 1, src, len(src)))
    return chunks


def chunk_python_file(path: Path, repo_name: str) -> list[Chunk]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return chunk_generic_lines(path, repo_name, text, "python")

    chunks, covered = [], set()

    def node_source(node):
        start = node.lineno
        end = getattr(node, "end_lineno", start)
        covered.update(range(start, end + 1))
        return start, end, "\n".join(lines[start - 1:end])

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start, end, src = node_source(node)
            chunks.append(Chunk(repo_name, str(path), "python", "function", node.name, start, end, src, len(src)))
        elif isinstance(node, ast.ClassDef):
            start, end, src = node_source(node)
            chunks.append(Chunk(repo_name, str(path), "python", "class", node.name, start, end, src, len(src)))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    s2, e2, src2 = node_source(sub)
                    chunks.append(Chunk(repo_name, str(path), "python", "method",
                                         f"{node.name}.{sub.name}", s2, e2, src2, len(src2)))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            start, end, src = node_source(node)
            if isinstance(node, ast.Assign) and node.targets:
                name = getattr(node.targets[0], "id", "constant")
            elif isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None):
                name = node.target.id
            else:
                name = "constant"
            chunks.append(Chunk(repo_name, str(path), "python", "constant", name, start, end, src, len(src)))

    leftover = [i + 1 for i in range(len(lines)) if (i + 1) not in covered]
    if leftover:
        start, end = min(leftover), max(leftover)
        src = "\n".join(lines[start - 1:end])
        if src.strip():
            chunks.append(Chunk(repo_name, str(path), "python", "block", "module_level", start, end, src, len(src)))
    return chunks


def chunk_generic_symbols(path: Path, repo_name: str, language: str) -> list[Chunk]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    pattern = GENERIC_FUNC_PATTERNS.get(language)
    if pattern is None:
        return chunk_generic_lines(path, repo_name, text, language)

    starts = [i for i, line in enumerate(lines) if pattern.search(line)]
    if not starts:
        return chunk_generic_lines(path, repo_name, text, language)

    chunks = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] - 1 if idx + 1 < len(starts) else len(lines) - 1
        while end > start and not lines[end].strip():
            end -= 1
        src = "\n".join(lines[start:end + 1])
        m = pattern.search(lines[start])
        name = next((g for g in m.groups() if g and re.match(r"^\w+$", g)), "anonymous")
        chunks.append(Chunk(repo_name, str(path), language, "function", name, start + 1, end + 1, src, len(src)))
    return chunks


def chunk_file(path: Path, repo_name: str) -> list[Chunk]:
    ext = path.suffix.lower()
    language = CODE_ONLY_MAP.get(ext, "text")
    if language == "python":
        return chunk_python_file(path, repo_name)
    if language in GENERIC_FUNC_PATTERNS:
        return chunk_generic_symbols(path, repo_name, language)
    if ext == ".md" or path.name.lower().startswith("readme"):
        return chunk_markdown_file(path, repo_name)
    if ext in DOC_EXTENSIONS:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return chunk_generic_lines(path, repo_name, text, DOC_EXTENSIONS[ext])
    text = path.read_text(encoding="utf-8", errors="ignore")
    return chunk_generic_lines(path, repo_name, text, language)


def chunk_repo(repo_dir: Path, repo_name: str) -> list[Chunk]:
    all_chunks = []
    for f in discover_files(repo_dir):
        for c in chunk_file(f, repo_name):
            all_chunks.extend(split_oversized(c))
    return all_chunks
