import ast
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from config import settings

MAX_CHUNK_CHARS = settings.MAX_CHUNK_CHARS

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

# Regex symbol splitters for non-Python languages (Python uses the AST instead).
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
    symbol_type: str  # function | class | method | constant | block | doc_section
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
            if not (ext in CODE_ONLY_MAP or ext in DOC_EXTENSIONS or fn.lower() in PRIORITY_DOC_FILENAMES):
                continue
            full = Path(root) / fn
            try:
                if full.stat().st_size > max_file_kb * 1024:
                    continue
            except OSError:
                continue
            files.append(full)
    return files


def split_oversized(chunk: "Chunk", max_chars: int = MAX_CHUNK_CHARS) -> list["Chunk"]:
    """Split a chunk into `<name>_partN` pieces of roughly max_chars each."""
    if chunk.char_count <= max_chars:
        return [chunk]
    out, buf, buf_start, cur_len = [], [], chunk.start_line, 0

    def emit():
        src = "\n".join(buf)
        out.append(Chunk(chunk.repo, chunk.file_path, chunk.language, chunk.symbol_type,
                         f"{chunk.symbol_name}_part{len(out) + 1}", buf_start,
                         buf_start + len(buf) - 1, src, len(src)))

    for i, line in enumerate(chunk.content.splitlines()):
        buf.append(line)
        cur_len += len(line) + 1
        if cur_len >= max_chars:
            emit()
            buf, buf_start, cur_len = [], chunk.start_line + i + 1, 0
    if buf:
        emit()
    return out


def chunk_generic_lines(path: Path, repo_name: str, text: str, language: str,
                        window: int = 60, overlap: int = 10) -> list[Chunk]:
    """Sliding-window fallback for files without symbol-level parsing."""
    lines = text.splitlines()
    chunks, i, n = [], 0, len(lines)
    while i < n:
        end = min(i + window, n)
        src = "\n".join(lines[i:end])
        if src.strip():
            chunks.append(Chunk(repo_name, str(path), language, "block",
                                f"lines_{i + 1}-{end}", i + 1, end, src, len(src)))
        if end == n:
            break
        i += window - overlap
    return chunks


def chunk_markdown_file(path: Path, repo_name: str) -> list[Chunk]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    header = re.compile(r"^#{1,3}\s+(.+)")
    starts = [i for i, line in enumerate(lines) if header.match(line)]
    if not starts:
        return chunk_generic_lines(path, repo_name, text, "markdown", window=80, overlap=10)

    chunks = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] - 1 if idx + 1 < len(starts) else len(lines) - 1
        while end > start and not lines[end].strip():
            end -= 1
        src = "\n".join(lines[start:end + 1])
        name = header.match(lines[start]).group(1).strip()
        chunks.append(Chunk(repo_name, str(path), "markdown", "doc_section",
                            name, start + 1, end + 1, src, len(src)))
    return chunks


def _node_start(node) -> int:
    """First line of a node including its decorators (ast gives the `def` line otherwise)."""
    return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])


def chunk_python_file(path: Path, repo_name: str) -> list[Chunk]:
    """One chunk per function / method / constant. Classes with methods get a header-only chunk
    (docstring + attributes) plus one chunk per method, so code is never indexed twice.
    Remaining top-level code becomes contiguous `module_level_<start>-<end>` blocks."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return chunk_generic_lines(path, repo_name, text, "python")

    chunks, covered = [], set()

    def add(kind, name, start, end):
        covered.update(range(start, end + 1))
        src = "\n".join(lines[start - 1:end])
        chunks.append(Chunk(repo_name, str(path), "python", kind, name, start, end, src, len(src)))

    funcs = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        start, end = _node_start(node), node.end_lineno
        if isinstance(node, funcs):
            add("function", node.name, start, end)
        elif isinstance(node, ast.ClassDef):
            methods = [s for s in node.body if isinstance(s, funcs)]
            if methods:
                header_end = _node_start(methods[0]) - 1
                if header_end >= start:
                    add("class", node.name, start, header_end)
                for sub in methods:
                    add("method", f"{node.name}.{sub.name}", _node_start(sub), sub.end_lineno)
            else:
                add("class", node.name, start, end)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            add("constant", getattr(target, "id", "constant"), start, end)

    # Flush contiguous runs of uncovered lines as module-level blocks
    run = []

    def flush():
        if run:
            body = lines[run[0] - 1:run[-1]]
            if any(l.strip() and not l.strip().startswith("#") for l in body):
                src = "\n".join(body)
                chunks.append(Chunk(repo_name, str(path), "python", "block",
                                    f"module_level_{run[0]}-{run[-1]}", run[0], run[-1], src, len(src)))
            run.clear()

    for ln in range(1, len(lines) + 1):
        if ln in covered:
            flush()
        else:
            run.append(ln)
    flush()
    return chunks


def chunk_generic_symbols(path: Path, repo_name: str, language: str) -> list[Chunk]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    pattern = GENERIC_FUNC_PATTERNS[language]
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
        chunks.append(Chunk(repo_name, str(path), language, "function",
                            name, start + 1, end + 1, src, len(src)))
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
    text = path.read_text(encoding="utf-8", errors="ignore")
    return chunk_generic_lines(path, repo_name, text, DOC_EXTENSIONS.get(ext, language))


def chunk_repo(repo_dir: Path, repo_name: str) -> list[Chunk]:
    return [piece for f in discover_files(repo_dir)
            for c in chunk_file(f, repo_name)
            for piece in split_oversized(c)]
