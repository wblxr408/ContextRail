"""Run explicitly configured local helpers, preserving their wire streams."""

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time


# Wait for the parent to put this process in its job before starting any worker.
# Read exactly one byte from the raw handle so the worker gets all of stdin.
_START_WORKER = r"""
import msvcrt, os, subprocess, sys
msvcrt.setmode(0, os.O_BINARY)
if os.read(0, 1) != b'\x01':
    raise SystemExit(125)
options = dict(stdin=0, stdout=1, stderr=2, creationflags=subprocess.CREATE_NO_WINDOW)
if sys.argv[1] == 'batch':
    raise SystemExit(subprocess.call(sys.argv[2], executable=sys.argv[3], **options))
raise SystemExit(subprocess.call(sys.argv[2:], **options))
"""


def _windows_worker(argv, env):
    program = shutil.which(argv[0], path=env.get("PATH"))
    if program is None or Path(program).suffix.lower() not in {".cmd", ".bat"}:
        return ["exec", *argv]

    # cmd.exe expands each value once with delayed expansion disabled. Quoting
    # the slots preserves metacharacters and literal percent signs in the argv.
    values = [str(Path(program).resolve()), *argv[1:]]
    if any(any(char in value for char in '"\r\n\0') for value in values):
        raise ValueError("Batch helper arguments cannot contain quotes, NUL, or newlines.")
    names = [f"__CONTEXTRAIL_BATCH_{index}" for index in range(len(values))]
    env.update(zip(names, values))
    command = " ".join(f'"%{name}%"' for name in names)
    cmd = str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe")
    return ["batch", f'"{cmd}" /d /v:off /s /c "{command}"', cmd]


@contextmanager
def _windows_job():
    import ctypes as ct
    from ctypes import wintypes as wt

    class BasicLimits(ct.Structure):
        _fields_ = [
            ("process_time", ct.c_int64), ("job_time", ct.c_int64), ("flags", wt.DWORD),
            ("min_working_set", ct.c_size_t), ("max_working_set", ct.c_size_t),
            ("process_limit", wt.DWORD), ("affinity", ct.c_size_t),
            ("priority", wt.DWORD), ("scheduling_class", wt.DWORD),
        ]

    class ExtendedLimits(ct.Structure):
        _fields_ = [
            ("basic", BasicLimits), ("io", ct.c_uint64 * 6),
            ("process_memory", ct.c_size_t), ("job_memory", ct.c_size_t),
            ("peak_process_memory", ct.c_size_t), ("peak_job_memory", ct.c_size_t),
        ]

    class Accounting(ct.Structure):
        _fields_ = [
            ("time", ct.c_int64 * 4), ("page_faults", wt.DWORD), ("total", wt.DWORD),
            ("active", wt.DWORD), ("terminated", wt.DWORD),
        ]

    api = ct.WinDLL("kernel32", use_last_error=True)
    for function, arguments, result in (
        (api.CreateJobObjectW, [ct.c_void_p, wt.LPCWSTR], wt.HANDLE),
        (api.SetInformationJobObject, [wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD], wt.BOOL),
        (api.QueryInformationJobObject, [wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD, ct.c_void_p], wt.BOOL),
        (api.OpenProcess, [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
        (api.AssignProcessToJobObject, [wt.HANDLE, wt.HANDLE], wt.BOOL),
        (api.TerminateJobObject, [wt.HANDLE, wt.UINT], wt.BOOL),
        (api.CloseHandle, [wt.HANDLE], wt.BOOL),
    ):
        function.argtypes, function.restype = arguments, result

    job = api.CreateJobObjectW(None, None)
    if not job:
        raise ct.WinError(ct.get_last_error())
    try:
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(job, 9, ct.byref(limits), ct.sizeof(limits)):
            raise ct.WinError(ct.get_last_error())

        def assign(pid):
            process = api.OpenProcess(0x100 | 0x1, False, pid)  # SET_QUOTA | TERMINATE
            if not process:
                raise ct.WinError(ct.get_last_error())
            try:
                if not api.AssignProcessToJobObject(job, process):
                    raise ct.WinError(ct.get_last_error())
            finally:
                api.CloseHandle(process)

        def stop():
            api.TerminateJobObject(job, 1)
            deadline = time.monotonic() + 2
            status = Accounting()
            while api.QueryInformationJobObject(job, 1, ct.byref(status), ct.sizeof(status), None) and status.active:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)

        yield assign, stop
    finally:
        api.CloseHandle(job)


def run_local(argv, *, input=None, env=None, cwd=None, timeout):
    """Capture byte streams separately and own the Windows helper process tree."""
    if os.name != "nt":
        return subprocess.run(argv, input=input, env=env, cwd=cwd, capture_output=True, timeout=timeout)

    env = dict(os.environ if env is None else env)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    args = [sys.executable, "-I", "-S", "-u", "-c", _START_WORKER, *_windows_worker(argv, env)]
    with _windows_job() as (assign, stop):
        with subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW) as process:
            try:
                assign(process.pid)
            except BaseException:
                process.kill()
                raise
            stream = process.stdin
            process.stdin = None  # communicate owns only the output pipes.
            write_errors = []

            def feed():
                try:
                    with stream:
                        stream.write(b"\x01")
                        stream.flush()
                        if input:
                            stream.write(input)
                except BrokenPipeError:
                    pass
                except OSError as exc:
                    if exc.errno != errno.EINVAL:
                        write_errors.append(exc)

            writer = threading.Thread(target=feed, daemon=True)
            deadline = time.monotonic() + timeout
            stopped = False
            try:
                writer.start()
                while True:
                    if process.poll() is not None:
                        # A background child can keep the output pipes open
                        # after the configured command has already exited.
                        stop()
                        stopped = True
                        stdout, stderr = process.communicate()
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(argv, timeout)
                    try:
                        stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
                writer.join()
                if write_errors:
                    raise write_errors[0]
                return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                stop()
                stopped = True
                stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr) from None
            finally:
                if not stopped:
                    stop()
                if writer.ident is not None:
                    writer.join()
                else:
                    stream.close()
