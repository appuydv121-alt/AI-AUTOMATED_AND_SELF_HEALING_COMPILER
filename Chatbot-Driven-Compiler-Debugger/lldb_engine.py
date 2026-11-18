def execute_lldb_command(executable_path: str, command: str) -> str:
    return "LLDB command execution is disabled; user programs run only in the isolated container."


def agentic_debug_loop(
    cpp_code: str,
    crash_output: str,
    executable_path: str = "./temp_program",
) -> tuple[str, str]:
    return (
        "LLDB inspection is disabled because user programs are executed in an isolated container.",
        "",
    )
