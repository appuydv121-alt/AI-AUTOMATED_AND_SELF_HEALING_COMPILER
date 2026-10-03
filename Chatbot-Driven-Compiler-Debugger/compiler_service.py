import subprocess
import os
import time


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
    """
    def __new__(cls, status_code: int, stdout: str, stderr: str, exit_code: int = 0, execution_time: float = 0.0, timed_out: bool = False):
        return super().__new__(cls, (status_code, stdout, stderr))

    def __init__(self, status_code: int, stdout: str, stderr: str, exit_code: int = 0, execution_time: float = 0.0, timed_out: bool = False):
        self.status_code = status_code
        self.return_code = status_code
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.execution_time = execution_time
        self.timed_out = timed_out


def compile_and_run(cpp_code: str, stdin_input: str = "", timeout_compile: int = 10, timeout_run: int = 5) -> RunResult:
    """
    Compiles with '-g' for LLDB support and runs the code with optional stdin input.
    Returns: RunResult (status_code, stdout, stderr)
      0 = Success
      1 = Compile Error
      2 = Runtime Error (Segfault/Crash/Non-zero Exit Code)
     -1 = Timeout/System Error
    """
    app_dir = os.path.dirname(os.path.abspath(__file__))
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
        # 1. COMPILE (Now with -g for debug symbols!)
        compile_process = subprocess.run(
            ["g++", "-g", source_file, "-o", executable_base], 
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
        run_process = subprocess.run(
            [binary_to_run],
            input=stdin_input,
            capture_output=True, 
            text=True, 
            timeout=timeout_run
        )
        execution_time = time.time() - run_start

        # 3. CATCH RUNTIME CRASHES & NON-ZERO EXITS
        if run_process.returncode != 0:
            is_crash = run_process.returncode < 0 or (run_process.returncode & 0xFFFFFFFF) >= 0xC0000000
            if is_crash:
                crash_reason = run_process.stderr.strip() if run_process.stderr else f"Process crashed with exit code {run_process.returncode} (Likely Segmentation Fault)"
            else:
                crash_reason = run_process.stderr.strip() if run_process.stderr else f"Process exited with non-zero exit code {run_process.returncode}"
            return RunResult(
                2,
                run_process.stdout,
                crash_reason,
                exit_code=run_process.returncode,
                execution_time=execution_time,
            )

        # 4. GRACEFUL SUCCESSFUL EXIT (exit code 0)
        return RunResult(
            0,
            run_process.stdout,
            run_process.stderr,
            exit_code=0,
            execution_time=execution_time,
        )

    except subprocess.TimeoutExpired:
        return RunResult(
            -1,
            "",
            "Execution timed out. Your program may be waiting for more input or stuck in an infinite loop.",
            exit_code=-1,
            execution_time=float(timeout_run),
            timed_out=True,
        )
    except Exception as e:
        return RunResult(-1, "", f"❌ System Error: {str(e)}", exit_code=-1, execution_time=0.0)