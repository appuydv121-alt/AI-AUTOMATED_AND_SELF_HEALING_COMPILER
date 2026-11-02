import subprocess
import os
import threading
import time
from secure_scan import run_security_guardrail

DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024
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
) -> tuple[int, str, str, bool, bool]:
    """Run a process while retaining bounded stdout/stderr and draining both pipes."""
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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


def compile_and_run(
    cpp_code: str,
    stdin_input: str = "",
    timeout_compile: int = 10,
    timeout_run: float = 5,
    language: str = "cpp",
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> RunResult:
    """
    Compiles and runs code with optional stdin input across supported languages.
    Returns: RunResult (status_code, stdout, stderr)
      0 = Success
      1 = Compile / Syntax Error / Security Block
      2 = Runtime Error (Exception/Crash/Non-zero Exit Code)
     -1 = Timeout/System Error
    """
    app_dir = os.path.dirname(os.path.abspath(__file__))
    if max_output_bytes < 1:
        raise ValueError("max_output_bytes must be greater than zero.")

    # Edge case: cap input at ~100KB
    if stdin_input and len(stdin_input) > 100 * 1024:
        stdin_input = stdin_input[:100 * 1024]
    if stdin_input and not stdin_input.endswith("\n"):
        stdin_input = stdin_input + "\n"
    elif not stdin_input:
        stdin_input = ""

    # ── Python execution branch ──────────────────────────────────────────────────
    if language.lower() in ("python", "py"):
        import ast
        import sys
        try:
            ast.parse(cpp_code)
        except SyntaxError as e:
            err_line = e.lineno or 1
            line_text = (e.text or "").strip()
            syntax_err = f"File \"temp_source.py\", line {err_line}\n    {line_text}\nSyntaxError: {e.msg}"
            return RunResult(1, "", syntax_err, exit_code=1, execution_time=0.0)

        py_source = os.path.join(app_dir, "temp_source.py")
        with open(py_source, "w", encoding="utf-8") as f:
            f.write(cpp_code)

        run_start = time.time()
        try:
            run_proc = subprocess.run(
                [sys.executable, py_source],
                input=stdin_input,
                capture_output=True,
                text=True,
                timeout=timeout_run,
            )
            elapsed = time.time() - run_start
            if run_proc.returncode != 0:
                return RunResult(2, run_proc.stdout, run_proc.stderr, exit_code=run_proc.returncode, execution_time=elapsed)
            return RunResult(0, run_proc.stdout, run_proc.stderr, exit_code=0, execution_time=elapsed)
        except subprocess.TimeoutExpired:
            return RunResult(-1, "", f"Execution Timed Out: Script exceeded {timeout_run}s limit.", exit_code=-1, execution_time=time.time() - run_start, timed_out=True)
        except Exception as exc:
            return RunResult(-1, "", f"Execution Error: {str(exc)}", exit_code=-1, execution_time=time.time() - run_start)

    # ── JavaScript & TypeScript execution branch ──────────────────────────────────
    if language.lower() in ("javascript", "js", "typescript", "ts"):
        import shutil
        node_bin = shutil.which("node")
        if not node_bin:
            return RunResult(-1, "", "Node.js executable was not found on your system path. Please install Node.js to run JavaScript/TypeScript.", exit_code=-1)

        ext = "ts" if ("type" in language.lower() or "ts" in language.lower()) else "js"
        js_source = os.path.join(app_dir, f"temp_source.{ext}")
        with open(js_source, "w", encoding="utf-8") as f:
            f.write(cpp_code)

        # Static syntax check using node --check
        chk = subprocess.run([node_bin, "--check", js_source], capture_output=True, text=True)
        if chk.returncode != 0:
            return RunResult(1, "", chk.stderr or chk.stdout, exit_code=chk.returncode, execution_time=0.0)

        run_start = time.time()
        try:
            run_proc = subprocess.run(
                [node_bin, js_source],
                input=stdin_input,
                capture_output=True,
                text=True,
                timeout=timeout_run,
            )
            elapsed = time.time() - run_start
            if run_proc.returncode != 0:
                return RunResult(2, run_proc.stdout, run_proc.stderr, exit_code=run_proc.returncode, execution_time=elapsed)
            return RunResult(0, run_proc.stdout, run_proc.stderr, exit_code=0, execution_time=elapsed)
        except subprocess.TimeoutExpired:
            return RunResult(-1, "", f"Execution Timed Out: Script exceeded {timeout_run}s limit.", exit_code=-1, execution_time=time.time() - run_start, timed_out=True)
        except Exception as exc:
            return RunResult(-1, "", f"Execution Error: {str(exc)}", exit_code=-1, execution_time=time.time() - run_start)

    # ── SQL execution branch (in-memory SQLite) ───────────────────────────────────
    if language.lower() in ("sql",):
        import sqlite3
        run_start = time.time()
        try:
            conn = sqlite3.connect(":memory:")
            init_sandbox_db(conn)
            cursor = conn.cursor()
            output_parts = []
            for stmt in cpp_code.split(";"):
                clean_stmt = stmt.strip()
                if not clean_stmt:
                    continue
                cursor.execute(clean_stmt)
                if cursor.description:
                    headers = [col[0] for col in cursor.description]
                    rows = cursor.fetchall()
                    header_line = " | ".join(headers)
                    output_parts.append(header_line)
                    output_parts.append("-" * max(20, len(header_line)))
                    for row in rows:
                        output_parts.append(" | ".join(str(val) for val in row))
                    output_parts.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''})\n")
                else:
                    output_parts.append(f"Query OK, {cursor.rowcount} row(s) affected.\n")
            conn.commit()
            conn.close()
            elapsed = time.time() - run_start
            return RunResult(0, "\n".join(output_parts).strip(), "", exit_code=0, execution_time=elapsed)
        except Exception as exc:
            elapsed = time.time() - run_start
            return RunResult(1, "", f"SQL Error: {str(exc)}", exit_code=1, execution_time=elapsed)
    # 0. DEFENSE-IN-DEPTH: Static Pre-Execution Security Gate
    is_safe, warning_msg = run_security_guardrail(cpp_code)
    if not is_safe:
        return RunResult(
            1,
            "",
            f"🚫 SECURITY GUARDRAIL BLOCKED:\n{warning_msg}\n\nExecution was aborted before compiling or running to protect your system.",
            exit_code=1,
            execution_time=0.0
        )

    source_file = os.path.join(app_dir, "temp_source.cpp")
    executable_base = os.path.join(app_dir, "temp_program")
    executable_exe = executable_base + ".exe"

    # Remove any existing executable to prevent running stale binaries if compile fails
    for old_path in (executable_base, executable_exe):
        try:
            if os.path.isfile(old_path):
                os.remove(old_path)
        except OSError:
            pass

    with open(source_file, "w", encoding="utf-8") as f:
        f.write(cpp_code)

    # Edge case: cap input at ~100KB
    if stdin_input and len(stdin_input) > 100 * 1024:
        stdin_input = stdin_input[:100 * 1024]

    # Ensure input ends with a trailing newline if provided and missing one
    if stdin_input:
        if not stdin_input.endswith("\n"):
            stdin_input = stdin_input + "\n"
    else:
        stdin_input = ""

    compile_start = time.time()
    try:
        # 1. COMPILE (Now with -g for debug symbols and hardening flags)
        compile_process = subprocess.run(
            ["g++", "-g", "-fstack-protector-strong", "-fmax-errors=5", source_file, "-o", executable_base], 
            capture_output=True, 
            text=True, 
            timeout=timeout_compile
        )

        if compile_process.returncode != 0:
            compile_time = time.time() - compile_start
            return RunResult(
                1,
                "",
                compile_process.stderr,
                exit_code=compile_process.returncode,
                execution_time=compile_time,
            )

        # 2. RUN BINARY
        binary_to_run = executable_exe if (os.name == "nt" and os.path.isfile(executable_exe)) else (
            executable_base if os.path.isfile(executable_base) else executable_exe
        )
        run_start = time.time()
        return_code, stdout, stderr, timed_out, output_limited = _run_with_output_limit(
            [binary_to_run],
            stdin_input=stdin_input,
            timeout=timeout_run,
            max_output_bytes=max_output_bytes,
        )
        execution_time = time.time() - run_start

        if timed_out:
            return RunResult(
                -1,
                stdout,
                "Execution timed out. Your program may be waiting for more input or stuck in an infinite loop."
                + (
                    f"\nOutput capture was limited to {max_output_bytes // 1024} KB."
                    if output_limited
                    else ""
                ),
                exit_code=-1,
                execution_time=execution_time,
                timed_out=True,
                output_limited=output_limited,
            )

        # 3. CATCH RUNTIME CRASHES & NON-ZERO EXITS
        if return_code != 0:
            is_crash = return_code < 0 or (return_code & 0xFFFFFFFF) >= 0xC0000000
            if is_crash:
                crash_reason = stderr.strip() if stderr else f"Process crashed with exit code {return_code} (Likely Segmentation Fault)"
            else:
                crash_reason = stderr.strip() if stderr else f"Process exited with non-zero exit code {return_code}"
            return RunResult(
                2,
                stdout,
                crash_reason,
                exit_code=return_code,
                execution_time=execution_time,
                output_limited=output_limited,
            )

        # 4. GRACEFUL SUCCESSFUL EXIT (exit code 0)
        return RunResult(
            0,
            stdout,
            stderr,
            exit_code=0,
            execution_time=execution_time,
            output_limited=output_limited,
        )
    except Exception as e:
        return RunResult(-1, "", f"❌ System Error: {str(e)}", exit_code=-1, execution_time=0.0)