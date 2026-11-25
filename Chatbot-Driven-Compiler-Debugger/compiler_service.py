import ast
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from secure_scan import run_security_guardrail
from security_layer import evaluate_request_security

DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024
DEFAULT_MAX_CODE_BYTES = int(os.environ.get("MAX_CODE_BYTES", 2 * 1024 * 1024))  # 2 MB default (supports 10,000+ lines)
_OUTPUT_READ_CHUNK_BYTES = 8192


class RunResult(tuple):
    """
    Subclasses tuple so that existing code unpacking:
        status, stdout, stderr = compile_and_run(...)
    continues to work without any modifications, while exposing
    rich execution metadata as attributes:
        result.status_code / result.return_code
        result.stdout
        result.stderr
        result.exit_code
        result.execution_time
        result.timed_out
        result.output_limited
    """
    def __new__(
        cls,
        status_code: int,
        stdout: str,
        stderr: str,
        exit_code: int = 0,
        execution_time: float = 0.0,
        timed_out: bool = False,
        output_limited: bool = False,
    ):
        return super().__new__(cls, (status_code, stdout, stderr))

    def __init__(
        self,
        status_code: int,
        stdout: str,
        stderr: str,
        exit_code: int = 0,
        execution_time: float = 0.0,
        timed_out: bool = False,
        output_limited: bool = False,
    ):
        self.status_code = status_code
        self.return_code = status_code
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.execution_time = execution_time
        self.timed_out = timed_out
        self.output_limited = output_limited


def _run_with_output_limit(
    command: list[str],
    stdin_input: str,
    timeout: float,
    max_output_bytes: int,
    cwd: str | None = None,
) -> tuple[int, str, str, bool, bool]:
    """Run a process while retaining bounded stdout/stderr and draining both pipes."""
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
    )
    output_buffers = [bytearray(), bytearray()]
    output_size = 0
    output_lock = threading.Lock()
    output_limit_reached = threading.Event()
    stream_errors: list[OSError] = []

    def stop_process() -> None:
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                return

    def read_stream(stream, buffer: bytearray) -> None:
        nonlocal output_size
        try:
            while True:
                chunk = stream.read(_OUTPUT_READ_CHUNK_BYTES)
                if not chunk:
                    return
                with output_lock:
                    available = max_output_bytes - output_size
                    if available > 0:
                        buffer.extend(chunk[:available])
                        output_size += min(len(chunk), available)
                    exceeded_limit = len(chunk) > available
                if exceeded_limit:
                    output_limit_reached.set()
        except OSError as error:
            stream_errors.append(error)

    def write_stdin() -> None:
        try:
            if stdin_input:
                process.stdin.write(stdin_input.encode("utf-8"))
                process.stdin.flush()
        except BrokenPipeError:
            pass
        except OSError as error:
            if process.poll() is None:
                stream_errors.append(error)
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    readers = [
        threading.Thread(target=read_stream, args=(process.stdout, output_buffers[0]), daemon=True),
        threading.Thread(target=read_stream, args=(process.stderr, output_buffers[1]), daemon=True),
    ]
    writer = threading.Thread(target=write_stdin, daemon=True)
    for reader in readers:
        reader.start()
    writer.start()

    timed_out = False
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            stop_process()
            break
        try:
            process.wait(timeout=min(remaining, 0.05))
        except subprocess.TimeoutExpired:
            continue

    if timed_out:
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    writer.join()
    for reader in readers:
        reader.join()

    if stream_errors:
        raise OSError(f"Could not capture process output: {stream_errors[0]}")

    output_limited = output_limit_reached.is_set()
    stdout = output_buffers[0].decode("utf-8", errors="replace")
    stderr = output_buffers[1].decode("utf-8", errors="replace")
    if output_limited:
        notice = (
            f"Output capture limited to {max_output_bytes // 1024} KB; "
            "output beyond this amount was discarded."
        )
        stderr = f"{stderr}\n{notice}" if stderr else notice
    return process.returncode, stdout, stderr, timed_out, output_limited


def init_sandbox_db(conn) -> None:
    """Seed in-memory SQLite sandbox with standard practice tables (users, students, products)."""
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            email TEXT,
            role TEXT DEFAULT 'user',
            active INTEGER DEFAULT 1
        );
        INSERT OR IGNORE INTO users (id, username, password, email, role, active) VALUES
            (1, 'admin', 'admin123', 'admin@example.com', 'admin', 1),
            (2, 'alice', 'alice_secret', 'alice@example.com', 'user', 1),
            (3, 'bob', 'bob_pass', 'bob@example.com', 'user', 1),
            (4, 'charlie', 'charlie99', 'charlie@example.com', 'moderator', 1),
            (5, 'vishal', 'vishal_pwd', 'vishal@example.com', 'developer', 1);

        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            grade INTEGER,
            department TEXT
        );
        INSERT OR IGNORE INTO students (id, name, grade, department) VALUES
            (1, 'Arpit', 92, 'CSE'),
            (2, 'Vishal', 88, 'IT'),
            (3, 'Rohan', 85, 'ECE');

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL,
            stock INTEGER
        );
        INSERT OR IGNORE INTO products (id, name, price, stock) VALUES
            (1, 'Laptop', 1200.0, 15),
            (2, 'Headphones', 75.5, 40),
            (3, 'Keyboard', 45.0, 25);
    """)
    conn.commit()


def _execute_sql_sandbox(
    sql: str,
    timeout: float,
    max_output_bytes: int,
) -> RunResult:
    allowed_tables = {"users", "students", "products", "sqlite_sequence"}
    denied_actions = {
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_PRAGMA,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_CREATE_VTABLE,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_DROP_VTABLE,
        sqlite3.SQLITE_ALTER_TABLE,
    }

    def authorize(action, arg1, arg2, _database, _trigger):
        if action in denied_actions:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ and arg1 not in allowed_tables:
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
            if arg1 not in allowed_tables:
                return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in {
            "load_extension",
            "readfile",
            "writefile",
            "randomblob",
            "zeroblob",
        }:
            return sqlite3.SQLITE_DENY
        allowed_actions = {
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_INSERT,
            sqlite3.SQLITE_UPDATE,
            sqlite3.SQLITE_DELETE,
            sqlite3.SQLITE_FUNCTION,
        }
        if hasattr(sqlite3, "SQLITE_RECURSIVE"):
            allowed_actions.add(sqlite3.SQLITE_RECURSIVE)
        return sqlite3.SQLITE_OK if action in allowed_actions else sqlite3.SQLITE_DENY

    started_at = time.monotonic()
    deadline = started_at + max(0.1, timeout)
    output_parts: list[str] = []
    output_size = 0
    try:
        with sqlite3.connect(":memory:") as connection:
            init_sandbox_db(connection)
            limits = {
                "SQLITE_LIMIT_LENGTH": 1024 * 1024,
                "SQLITE_LIMIT_SQL_LENGTH": 64 * 1024,
                "SQLITE_LIMIT_COLUMN": 64,
                "SQLITE_LIMIT_EXPR_DEPTH": 100,
                "SQLITE_LIMIT_COMPOUND_SELECT": 10,
                "SQLITE_LIMIT_VDBE_OP": 100_000,
                "SQLITE_LIMIT_FUNCTION_ARG": 16,
                "SQLITE_LIMIT_VARIABLE_NUMBER": 1000,
                "SQLITE_LIMIT_TRIGGER_DEPTH": 0,
                "SQLITE_LIMIT_ATTACHED": 0,
            }
            for name, value in limits.items():
                category = getattr(sqlite3, name, None)
                if category is not None and hasattr(connection, "setlimit"):
                    connection.setlimit(category, value)
            connection.set_authorizer(authorize)
            connection.set_progress_handler(
                lambda: int(time.monotonic() >= deadline),
                1000,
            )
            statements: list[str] = []
            pending_parts: list[str] = []
            for character in sql:
                pending_parts.append(character)
                if character == ";" and sqlite3.complete_statement("".join(pending_parts)):
                    pending = "".join(pending_parts)
                    if pending.strip():
                        statements.append(pending.strip())
                    pending_parts = []
            pending = "".join(pending_parts)
            if pending.strip():
                statements.append(pending.strip())

            for statement in statements:
                cursor = connection.execute(statement)
                if cursor.description:
                    headers = [column[0] for column in cursor.description]
                    header_line = " | ".join(headers)
                    lines = [header_line, "-" * max(20, len(header_line))]
                    rendered_size = sum(len(line.encode("utf-8")) + 1 for line in lines)
                    row_count = 0
                    for row in cursor:
                        if row_count >= 1000:
                            raise ValueError("Query returned more than 1000 rows; output was blocked.")
                        row_line = " | ".join(str(value) for value in row)
                        row_size = len(row_line.encode("utf-8")) + 1
                        if output_size + rendered_size + row_size > max_output_bytes:
                            raise ValueError("SQL result exceeded the output capture limit.")
                        lines.append(row_line)
                        rendered_size += row_size
                        row_count += 1
                    row_count_line = f"({row_count} row{'s' if row_count != 1 else ''})"
                    if output_size + rendered_size + len(row_count_line.encode("utf-8")) > max_output_bytes:
                        raise ValueError("SQL result exceeded the output capture limit.")
                    lines.append(row_count_line)
                    rendered = "\n".join(lines)
                else:
                    rendered = f"Query OK, {cursor.rowcount} row(s) affected."
                encoded_size = len(rendered.encode("utf-8"))
                separator_size = 2 if output_parts else 0
                if output_size + encoded_size + separator_size > max_output_bytes:
                    raise ValueError("SQL result exceeded the output capture limit.")
                output_parts.append(rendered)
                output_size += encoded_size + separator_size
        return RunResult(
            0,
            "\n\n".join(output_parts),
            "",
            exit_code=0,
            execution_time=time.monotonic() - started_at,
        )
    except Exception as exc:
        return RunResult(
            1,
            "",
            f"SQL blocked or failed: {exc}",
            exit_code=1,
            execution_time=time.monotonic() - started_at,
        )


def _run_local_process(
    source: str,
    language: str,
    stdin_input: str,
    timeout_compile: int,
    timeout_run: float,
    max_output_bytes: int,
    sanitizers: bool = False,
) -> RunResult:
    """Run code locally using system compilers and interpreters with timeouts and output bounds."""
    # ── C / C++ ───────────────────────────────────────────────────────────────
    if language == "cpp":
        gpp = shutil.which("g++") or shutil.which("clang++")
        if not gpp:
            return RunResult(
                1,
                "",
                "g++ compiler not found in system PATH. Install MinGW-w64 / MSYS2 or Xcode/build-essential.",
                exit_code=1,
            )

        with tempfile.TemporaryDirectory(prefix="compiler-local-") as work_dir:
            source_path = os.path.join(work_dir, "source.cpp")
            exe_name = "program.exe" if os.name == "nt" else "program"
            exe_path = os.path.join(work_dir, exe_name)

            with open(source_path, "w", encoding="utf-8") as f:
                f.write(source)

            compile_cmd = [
                gpp,
                "-std=c++17",
                "-O0",
                "-g",
                "-fstack-protector-strong",
                "-fmax-errors=5",
            ]
            if sanitizers:
                compile_cmd.extend([
                    "-Wall",
                    "-Wextra",
                    "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer",
                ])
            compile_cmd.extend([source_path, "-o", exe_path])

            compile_start = time.monotonic()
            ret, c_stdout, c_stderr, c_timed_out, c_limited = _run_with_output_limit(
                compile_cmd,
                stdin_input="",
                timeout=float(timeout_compile),
                max_output_bytes=max_output_bytes,
                cwd=work_dir,
            )

            if c_timed_out:
                return RunResult(
                    1,
                    "",
                    f"Compilation timed out after {timeout_compile}s.",
                    exit_code=124,
                    execution_time=time.monotonic() - compile_start,
                    timed_out=True,
                )
            if ret != 0:
                return RunResult(
                    1,
                    c_stdout,
                    c_stderr,
                    exit_code=ret,
                    execution_time=time.monotonic() - compile_start,
                )

            # Execute compiled binary
            run_start = time.monotonic()
            r_ret, r_stdout, r_stderr, r_timed_out, r_limited = _run_with_output_limit(
                [exe_path],
                stdin_input=stdin_input,
                timeout=float(timeout_run),
                max_output_bytes=max_output_bytes,
                cwd=work_dir,
            )
            run_time = time.monotonic() - run_start

            if r_timed_out:
                return RunResult(
                    -1,
                    r_stdout,
                    "Execution timed out. Your program may be waiting for more input or stuck in an infinite loop.",
                    exit_code=-1,
                    execution_time=run_time,
                    timed_out=True,
                    output_limited=r_limited,
                )

            if r_ret != 0:
                is_crash = r_ret < 0 or (r_ret & 0xFFFFFFFF) >= 0xC0000000
                crash_msg = (
                    f"Process crashed with exit code {r_ret} (Segmentation Fault or unhandled signal)"
                    if is_crash
                    else f"Process exited with non-zero exit code {r_ret}"
                )
                err_detail = f"{r_stderr.strip()}\n{crash_msg}".strip() if r_stderr.strip() else crash_msg
                return RunResult(
                    2,
                    r_stdout,
                    err_detail,
                    exit_code=r_ret,
                    execution_time=run_time,
                    output_limited=r_limited,
                )

            return RunResult(
                0,
                r_stdout,
                r_stderr,
                exit_code=0,
                execution_time=run_time,
                output_limited=r_limited,
            )

    # ── Python ────────────────────────────────────────────────────────────────
    elif language == "python":
        try:
            ast.parse(source)
        except SyntaxError as e:
            err_line = e.lineno or 1
            line_text = (e.text or "").strip()
            syntax_err = f'File "source.py", line {err_line}\n    {line_text}\nSyntaxError: {e.msg}'
            return RunResult(1, "", syntax_err, exit_code=1)

        with tempfile.TemporaryDirectory(prefix="python-local-") as work_dir:
            source_path = os.path.join(work_dir, "source.py")
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(source)

            run_start = time.monotonic()
            ret, stdout, stderr, timed_out, limited = _run_with_output_limit(
                [sys.executable, "-B", source_path],
                stdin_input=stdin_input,
                timeout=float(timeout_run),
                max_output_bytes=max_output_bytes,
                cwd=work_dir,
            )
            run_time = time.monotonic() - run_start

            if timed_out:
                return RunResult(
                    -1,
                    stdout,
                    f"Execution Timed Out: Python script exceeded {timeout_run}s limit.",
                    exit_code=-1,
                    execution_time=run_time,
                    timed_out=True,
                    output_limited=limited,
                )
            if ret != 0:
                return RunResult(
                    2,
                    stdout,
                    stderr,
                    exit_code=ret,
                    execution_time=run_time,
                    output_limited=limited,
                )
            return RunResult(
                0,
                stdout,
                stderr,
                exit_code=0,
                execution_time=run_time,
                output_limited=limited,
            )

    # ── JavaScript ────────────────────────────────────────────────────────────
    elif language == "javascript":
        node_bin = shutil.which("node")
        if not node_bin:
            return RunResult(
                1,
                "",
                "Node.js runtime not found in system PATH. Install Node.js from https://nodejs.org.",
                exit_code=1,
            )

        with tempfile.TemporaryDirectory(prefix="js-local-") as work_dir:
            source_path = os.path.join(work_dir, "source.js")
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(source)

            # Static syntax check
            chk_ret, chk_out, chk_err, _, _ = _run_with_output_limit(
                [node_bin, "--check", source_path],
                stdin_input="",
                timeout=float(timeout_compile),
                max_output_bytes=max_output_bytes,
                cwd=work_dir,
            )
            if chk_ret != 0:
                return RunResult(1, "", chk_err or chk_out, exit_code=chk_ret)

            run_start = time.monotonic()
            ret, stdout, stderr, timed_out, limited = _run_with_output_limit(
                [node_bin, source_path],
                stdin_input=stdin_input,
                timeout=float(timeout_run),
                max_output_bytes=max_output_bytes,
                cwd=work_dir,
            )
            run_time = time.monotonic() - run_start

            if timed_out:
                return RunResult(
                    -1,
                    stdout,
                    f"Execution Timed Out: JavaScript script exceeded {timeout_run}s limit.",
                    exit_code=-1,
                    execution_time=run_time,
                    timed_out=True,
                    output_limited=limited,
                )
            if ret != 0:
                return RunResult(
                    2,
                    stdout,
                    stderr,
                    exit_code=ret,
                    execution_time=run_time,
                    output_limited=limited,
                )
            return RunResult(
                0,
                stdout,
                stderr,
                exit_code=0,
                execution_time=run_time,
                output_limited=limited,
            )

    # ── TypeScript ────────────────────────────────────────────────────────────
    elif language == "typescript":
        node_bin = shutil.which("node")
        tsc_bin = shutil.which("tsc")
        if not node_bin:
            return RunResult(1, "", "Node.js runtime not found in system PATH.", exit_code=1)

        with tempfile.TemporaryDirectory(prefix="ts-local-") as work_dir:
            source_path = os.path.join(work_dir, "source.ts")
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(source)

            if tsc_bin:
                compile_cmd = [
                    tsc_bin,
                    "--target", "ES2020",
                    "--module", "commonjs",
                    "--skipLibCheck",
                    "--outDir", work_dir,
                    source_path,
                ]
                t_ret, t_out, t_err, t_timeout, _ = _run_with_output_limit(
                    compile_cmd,
                    stdin_input="",
                    timeout=float(timeout_compile),
                    max_output_bytes=max_output_bytes,
                    cwd=work_dir,
                )
                if t_ret != 0:
                    return RunResult(1, t_out, t_err, exit_code=t_ret)

                js_path = os.path.join(work_dir, "source.js")
                run_start = time.monotonic()
                ret, stdout, stderr, timed_out, limited = _run_with_output_limit(
                    [node_bin, js_path],
                    stdin_input=stdin_input,
                    timeout=float(timeout_run),
                    max_output_bytes=max_output_bytes,
                    cwd=work_dir,
                )
                run_time = time.monotonic() - run_start
                if timed_out:
                    return RunResult(-1, stdout, "Execution Timed Out.", exit_code=-1, execution_time=run_time, timed_out=True)
                if ret != 0:
                    return RunResult(2, stdout, stderr, exit_code=ret, execution_time=run_time)
                return RunResult(0, stdout, stderr, exit_code=0, execution_time=run_time)
            else:
                return RunResult(1, "", "TypeScript compiler 'tsc' not found. Install via 'npm install -g typescript'.", exit_code=1)

    return RunResult(-1, "", f"Unsupported language: {language}", exit_code=-1)


def _run_in_docker_sandbox(
    cpp_code: str,
    stdin_input: str,
    timeout_compile: int,
    timeout_run: float,
    max_output_bytes: int,
) -> RunResult | None:
    """Execute C++ code inside an isolated Docker + Seccomp container when enabled."""
    if os.getenv("TOACD_DOCKER_SANDBOX", "0") != "1":
        return None
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return None

    image = os.getenv("TOACD_SANDBOX_IMAGE", "toacd-cpp-sandbox:latest")
    seccomp_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "security", "seccomp-cpp.json"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "main.cpp")
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(cpp_code)

        cmd = [
            docker_bin, "run", "--rm", "-i",
            "--network=none",
            "--memory=128m",
            "--cpus=0.5",
            "--pids-limit=32",
            "--security-opt", "no-new-privileges",
        ]
        if os.path.exists(seccomp_path):
            cmd.extend(["--security-opt", f"seccomp={seccomp_path}"])

        shell_cmd = (
            "g++ -Wall -Wextra -std=c++17 /sandbox/main.cpp -o /tmp/prog 2>/tmp/cerr.txt "
            "|| { cat /tmp/cerr.txt >&2; exit 101; }; "
            "/tmp/prog"
        )
        cmd.extend(["-v", f"{tmpdir}:/sandbox:ro", image, shell_cmd])

        start_t = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                input=stdin_input.encode("utf-8"),
                capture_output=True,
                timeout=timeout_compile + timeout_run,
            )
            elapsed = round(time.perf_counter() - start_t, 4)
            stdout = proc.stdout[:max_output_bytes].decode("utf-8", errors="replace")
            stderr = proc.stderr[:max_output_bytes].decode("utf-8", errors="replace")
            if proc.returncode == 101:
                return RunResult(1, stdout, stderr, exit_code=1, execution_time=elapsed)
            if proc.returncode != 0:
                return RunResult(2, stdout, stderr, exit_code=proc.returncode, execution_time=elapsed)
            return RunResult(0, stdout, stderr, exit_code=0, execution_time=elapsed)
        except subprocess.TimeoutExpired:
            elapsed = round(time.perf_counter() - start_t, 4)
            return RunResult(-1, "", "Docker sandbox execution timed out.", exit_code=-1, execution_time=elapsed, timed_out=True)


def compile_and_run(
    cpp_code: str,
    stdin_input: str = "",
    timeout_compile: int = 10,
    timeout_run: float = 5,
    language: str = "cpp",
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    sanitizers: bool = False,
    client_id: str = "local-session",
) -> RunResult:
    """Compile and execute code locally with resource limits and multi-layer security guardrails."""
    if not isinstance(cpp_code, str):
        return RunResult(1, "", "Execution blocked: source code must be text.", exit_code=1)
    if max_output_bytes < 1:
        raise ValueError("max_output_bytes must be greater than zero.")
    max_output_bytes = min(max_output_bytes, DEFAULT_MAX_OUTPUT_BYTES)
    if len(cpp_code.encode("utf-8")) > DEFAULT_MAX_CODE_BYTES:
        limit_mb = DEFAULT_MAX_CODE_BYTES // (1024 * 1024)
        return RunResult(1, "", f"Execution blocked: source code exceeds the {limit_mb} MB limit.", exit_code=1)

    aliases = {
        "c": "cpp",
        "c++": "cpp",
        "cpp": "cpp",
        "python": "python",
        "py": "python",
        "javascript": "javascript",
        "js": "javascript",
        "typescript": "typescript",
        "ts": "typescript",
        "sql": "sql",
    }
    normalized_language = aliases.get(language.strip().lower())
    if normalized_language is None:
        return RunResult(
            -1,
            "",
            f"Execution blocked: unsupported language '{language}'.",
            exit_code=-1,
        )

    if sanitizers and normalized_language != "cpp":
        return RunResult(
            -1,
            "",
            "Sanitizer execution is supported only for C++ code.",
            exit_code=-1,
        )

    # 0A. PERIMETER & BEHAVIORAL SECURITY LAYER (RateLimit/Fail2ban -> OWASP WAF -> IsolationForest)
    sec_res = evaluate_request_security(
        cpp_code,
        stdin_input=stdin_input,
        client_id=client_id,
    )
    if not sec_res.allowed:
        return RunResult(
            1,
            "",
            f"🚫 PERIMETER SECURITY BLOCKED ({sec_res.blocked_by}):\n{sec_res.reason}\n\nExecution was aborted to protect the system.",
            exit_code=1,
            execution_time=0.0,
        )

    # 0B. DEFENSE-IN-DEPTH: Static Pre-Execution Security Gate
    is_safe, warning_msg = run_security_guardrail(cpp_code)
    if not is_safe:
        return RunResult(
            1,
            "",
            f"🚫 SECURITY GUARDRAIL BLOCKED:\n{warning_msg}\n\nExecution was aborted before compiling or running to protect your system.",
            exit_code=1,
            execution_time=0.0,
        )

    # Edge case: cap input at ~100KB
    if stdin_input and len(stdin_input) > 100 * 1024:
        stdin_input = stdin_input[:100 * 1024]

    # Ensure input ends with a trailing newline if provided and missing one
    if stdin_input:
        if not stdin_input.endswith("\n"):
            stdin_input = stdin_input + "\n"
    else:
        stdin_input = ""

    # SQL in-memory restricted execution
    if normalized_language == "sql":
        return _execute_sql_sandbox(
            cpp_code,
            timeout=timeout_run,
            max_output_bytes=max_output_bytes,
        )

    # Optional Docker + Seccomp Sandbox (when TOACD_DOCKER_SANDBOX=1 and Docker is available)
    if normalized_language == "cpp" and not sanitizers:
        docker_res = _run_in_docker_sandbox(
            cpp_code,
            stdin_input,
            timeout_compile,
            timeout_run,
            max_output_bytes,
        )
        if docker_res is not None:
            return docker_res

    # Local compiled/interpreted execution
    return _run_local_process(
        cpp_code,
        normalized_language,
        stdin_input,
        timeout_compile,
        timeout_run,
        max_output_bytes,
        sanitizers=sanitizers,
    )
