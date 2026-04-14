"""
Hermes-style Tools for Supervisor CLI
File, Terminal, and Web tools
"""

import subprocess
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import structlog

logger = structlog.get_logger()


# ============ File Tools ============

@dataclass
class FileReadResult:
    """Result from file read"""
    content: str
    total_lines: int
    file_path: str
    size_bytes: int


def read_file(
    path: str,
    offset: int = 1,
    limit: int = 500
) -> FileReadResult:
    """
    Read a file with pagination
    
    Args:
        path: File path (absolute or relative)
        offset: Line number to start from (1-indexed)
        limit: Number of lines to read
    
    Returns:
        FileReadResult with content and metadata
    """
    file_path = Path(path).expanduser().resolve()
    
    if not file_path.exists():
        return FileReadResult(
            content=f"Error: File not found: {path}",
            total_lines=0,
            file_path=str(file_path),
            size_bytes=0
        )
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        total_lines = len(lines)
        start = max(0, offset - 1)
        end = min(start + limit, total_lines)
        
        content = ''.join(lines[start:end])
        size = file_path.stat().st_size
        
        return FileReadResult(
            content=content,
            total_lines=total_lines,
            file_path=str(file_path),
            size_bytes=size
        )
    except Exception as e:
        return FileReadResult(
            content=f"Error reading file: {str(e)}",
            total_lines=0,
            file_path=str(file_path),
            size_bytes=0
        )


def write_file(path: str, content: str) -> Dict[str, Any]:
    """
    Write content to a file (overwrites existing)
    
    Args:
        path: File path
        content: Content to write
    
    Returns:
        Result dict with status
    """
    file_path = Path(path).expanduser().resolve()
    
    try:
        # Create parent directories
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return {
            "success": True,
            "path": str(file_path),
            "size": len(content)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def search_files(
    pattern: str,
    path: str = ".",
    target: str = "content",
    file_glob: Optional[str] = None,
    limit: int = 50,
    context: int = 2
) -> Dict[str, Any]:
    """
    Search files by content or name
    
    Args:
        pattern: Regex pattern for content, or glob pattern for files
        path: Directory to search
        target: "content" or "files"
        file_glob: Filter by file pattern (e.g., "*.py")
        limit: Max results
        context: Lines of context around matches
    
    Returns:
        Dict with matches
    """
    search_path = Path(path).expanduser().resolve()
    
    if not search_path.exists():
        return {"matches": [], "error": "Path not found"}
    
    results = []
    
    if target == "files":
        # File name search (glob)
        for f in search_path.rglob(file_glob or pattern):
            if f.is_file():
                results.append(str(f))
                if len(results) >= limit:
                    break
    else:
        # Content search (grep-like)
        try:
            import re
            regex = re.compile(pattern)
        except re.error:
            return {"matches": [], "error": "Invalid regex pattern"}
        
        for f in search_path.rglob(file_glob or "*"):
            if not f.is_file():
                continue
            
            try:
                with open(f, 'r', encoding='utf-8', errors='ignore') as file:
                    for i, line in enumerate(file, 1):
                        if regex.search(line):
                            results.append(f"{f}:{i}: {line.strip()}")
                            if len(results) >= limit:
                                break
            except OSError:
                continue
    
    return {"matches": results, "total": len(results)}


def list_files(
    path: str = ".",
    pattern: Optional[str] = None,
    recursive: bool = True
) -> List[Dict[str, Any]]:
    """
    List files in a directory
    
    Args:
        path: Directory path
        pattern: Optional filter pattern
        recursive: Recursive listing
    
    Returns:
        List of file info dicts
    """
    search_path = Path(path).expanduser().resolve()
    
    if not search_path.exists():
        return []
    
    files = []
    iterator = search_path.rglob(pattern or "*") if recursive else search_path.glob(pattern or "*")
    
    for f in iterator:
        if f.is_file():
            stat = f.stat()
            files.append({
                "name": f.name,
                "path": str(f),
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })
    
    # Sort by modified time
    files.sort(key=lambda x: x["modified"], reverse=True)
    
    return files[:100]  # Limit to 100


# ============ Terminal Tools ============

def terminal(
    command: str,
    timeout: int = 180,
    workdir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute a terminal command
    
    Args:
        command: Command to execute
        timeout: Timeout in seconds
        workdir: Working directory
    
    Returns:
        Dict with output, exit_code
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workdir
        )
        
        return {
            "output": result.stdout,
            "error": result.stderr,
            "exit_code": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            "output": "",
            "error": f"Command timed out after {timeout}s",
            "exit_code": -1
        }
    except Exception as e:
        return {
            "output": "",
            "error": str(e),
            "exit_code": -1
        }


async def terminal_async(
    command: str,
    timeout: int = 180,
    workdir: Optional[str] = None
) -> Dict[str, Any]:
    """Async version of terminal"""
    return await asyncio.to_thread(terminal, command, timeout, workdir)


# ============ Web Tools ============

def web_search(query: str, limit: int = 10) -> Dict[str, Any]:
    """
    Search the web using DuckDuckGo
    
    Args:
        query: Search query
        limit: Max results
    
    Returns:
        List of search results
    """
    try:
        import httpx
    except ImportError:
        return {"results": [], "error": "httpx not installed"}
    
    try:
        # Simple search via HTML scrape (no API key needed)
        url = f"https://html.duckduckgo.com/html/?q={query}"
        
        response = httpx.get(url, timeout=10)
        results = []
        
        # Simple parsing (very basic)
        import re
        titles = re.findall(r'<a class="result__a"[^>]*href="([^"]*)"[^>]*>([^<]*)</a>', response.text)
        snippets = re.findall(r'<a class="result__snippet"[^>]*>([^<]*)</a>', response.text)
        
        for i, (url, title) in enumerate(titles[:limit]):
            snippet = snippets[i] if i < len(snippets) else ""
            results.append({
                "title": title,
                "url": url,
                "snippet": snippet
            })
        
        return {"results": results, "total": len(results)}
    except Exception as e:
        return {"results": [], "error": str(e)}


def fetch_url(url: str, timeout: int = 30) -> Dict[str, Any]:
    """
    Fetch content from a URL
    
    Args:
        url: URL to fetch
        timeout: Timeout in seconds
    
    Returns:
        Dict with content, status_code
    """
    try:
        import httpx
    except ImportError:
        return {"content": "", "error": "httpx not installed"}
    
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        
        return {
            "content": response.text[:50000],  # Limit to 50K
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "url": str(response.url)
        }
    except Exception as e:
        return {"content": "", "error": str(e)}


# ============ Code Execution ============

def execute_code(
    code: str,
    language: str = "python"
) -> Dict[str, Any]:
    """
    Execute code in a sandbox
    
    Args:
        code: Code to execute
        language: Language (python, bash)
    
    Returns:
        Dict with output, error
    """
    if language == "python":
        # Use exec in a restricted environment
        import io
        import sys
        
        stdout = io.StringIO()
        stderr = io.StringIO()
        
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        
        try:
            sys.stdout = stdout
            sys.stderr = stderr
            
            # Very restricted exec
            exec(code, {"__builtins__": {}})
            
            return {
                "output": stdout.getvalue(),
                "error": stderr.getvalue()
            }
        except Exception as e:
            return {
                "output": stdout.getvalue(),
                "error": str(e)
            }
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
    else:
        return terminal(command=code)


# ============ Tool Registry ============

# All tools available in CLI
CLI_TOOLS = {
    "read_file": {
        "name": "read_file",
        "description": "Read a file with pagination",
        "parameters": {
            "path": "str",
            "offset": "int",
            "limit": "int"
        }
    },
    "write_file": {
        "name": "write_file",
        "description": "Write content to a file",
        "parameters": {
            "path": "str",
            "content": "str"
        }
    },
    "search_files": {
        "name": "search_files",
        "description": "Search files by content or name",
        "parameters": {
            "pattern": "str",
            "path": "str",
            "target": "str"
        }
    },
    "terminal": {
        "name": "terminal",
        "description": "Execute a terminal command",
        "parameters": {
            "command": "str",
            "timeout": "int"
        }
    },
    "web_search": {
        "name": "web_search",
        "description": "Search the web",
        "parameters": {
            "query": "str",
            "limit": "int"
        }
    },
    "fetch_url": {
        "name": "fetch_url",
        "description": "Fetch content from URL",
        "parameters": {
            "url": "str"
        }
    },
    "execute_code": {
        "name": "execute_code",
        "description": "Execute Python code",
        "parameters": {
            "code": "str",
            "language": "str"
        }
    },
}


__all__ = [
    "read_file",
    "write_file",
    "search_files",
    "list_files",
    "terminal",
    "terminal_async",
    "web_search",
    "fetch_url",
    "execute_code",
    "CLI_TOOLS",
]
