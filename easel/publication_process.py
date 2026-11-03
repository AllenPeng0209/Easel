"""Awaitable publication process ownership, without interpreting platform success.

Python adaptation of DeepSeek Harness (ce7073d, MIT) subprocess-local's
managed-owner.waitForExit and bash-local's bounded output/TERM→KILL contract:
direct exit or closed pipes never alone prove the owned range has stopped;
unobservable termination rejects. POSIX ownership is a new process group.
Processes deliberately escaping that group need stronger OS containment.

Python asyncio subprocess lifecycle: https://docs.python.org/3/library/asyncio-subprocess.html
Windows tree termination: https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/taskkill
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import json
import math
import os
from pathlib import Path
import signal
import subprocess


class PublicationProcessStartError(RuntimeError):
    started = False
    cleanup_confirmed = True


class PublicationProcessCleanupError(RuntimeError):
    started = True
    cleanup_confirmed = False

    def __init__(self, message, *, pid=None):
        super().__init__(message)
        self.pid = pid


@dataclass(frozen=True)
class PublicationProcessResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    pid: int
    timed_out: bool = False
    output_truncated: bool = False
    started: bool = True
    cleanup_confirmed: bool = True


async def _uninterruptible(task):
    """One shared cleanup barrier survives repeated cancellation of its caller."""
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise PublicationProcessCleanupError('发布进程清理被中断，无法确认终止') from None


async def _control(argv, timeout=4):
    """Fixed local OS inspection commands; never publisher stdout parsing."""
    process = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    return process.returncode, stdout, stderr


def _signal_group(pid, sig):
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        # macOS may return EPERM for a group containing only init-owned zombie
        # descendants. The independent live-member observation decides success;
        # an actual permission-denied survivor will still fail closed there.
        pass


async def _posix_live(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    # Unlike killpg(0), ps lets a dead Linux grandchild awaiting init's reap be
    # distinguished from a process that can still perform platform effects.
    status, output, _ = await _control(['/bin/ps', '-axo', 'pid=,pgid=,stat='])
    if status != 0:
        raise PublicationProcessCleanupError('无法观察发布进程组的终止状态', pid=pid)
    for line in output.decode('ascii', 'strict').splitlines():
        fields = line.split()
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit():
            raise PublicationProcessCleanupError('发布进程组状态无法完整解析', pid=pid)
        if int(fields[1]) == pid and not fields[2].startswith('Z'):
            return True
    return False


async def _terminate_posix(process, grace):
    loop = asyncio.get_running_loop()
    _signal_group(process.pid, signal.SIGTERM)
    deadline = loop.time() + grace
    while await _posix_live(process.pid):
        if loop.time() >= deadline:
            break
        await asyncio.sleep(min(.025, max(0, deadline - loop.time())))
    if await _posix_live(process.pid):
        _signal_group(process.pid, signal.SIGKILL)
    deadline = loop.time() + 4
    while await _posix_live(process.pid):
        if loop.time() >= deadline:
            raise PublicationProcessCleanupError('发布进程组在强制终止后仍未退出', pid=process.pid)
        await asyncio.sleep(.025)


async def _windows_members(pid, known=()):
    system = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32'
    script = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | "
              "ConvertTo-Json -Compress")
    status, output, _ = await _control([str(system / 'WindowsPowerShell/v1.0/powershell.exe'),
        '-NoProfile', '-NonInteractive', '-Command', script])
    if status != 0:
        raise PublicationProcessCleanupError('无法观察 Windows 发布进程树', pid=pid)
    rows = json.loads(output.decode('utf-8-sig'))
    rows = rows if isinstance(rows, list) else [rows]
    if any(not isinstance(row, dict) or type(row.get('ProcessId')) is not int
           or type(row.get('ParentProcessId')) is not int for row in rows):
        raise PublicationProcessCleanupError('Windows 发布进程树状态无效', pid=pid)
    members = {pid, *known}
    while True:
        expanded = members | {row['ProcessId'] for row in rows if row['ParentProcessId'] in members}
        if expanded == members:
            break
        members = expanded
    present = {row['ProcessId'] for row in rows}
    return members & present


async def _terminate_windows(process):
    # /T targets the actual descendant tree; confirmation is a separate OS
    # inventory read rather than the localized taskkill success message.
    taskkill = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/taskkill.exe')
    members = await _windows_members(process.pid)
    for pid in [process.pid, *sorted(members - {process.pid}, reverse=True)]:
        await _control([taskkill, '/PID', str(pid), '/T', '/F'])
    deadline = asyncio.get_running_loop().time() + 4
    while remaining := await _windows_members(process.pid, members):
        members |= remaining
        if asyncio.get_running_loop().time() >= deadline:
            raise PublicationProcessCleanupError('Windows 发布进程树在终止后仍未退出', pid=process.pid)
        await asyncio.sleep(.05)


async def _read_bounded(stream, limit):
    captured = bytearray()
    truncated = False
    while chunk := await stream.read(65536):
        remaining = max(0, limit - len(captured))
        captured.extend(chunk[:remaining])
        truncated |= len(chunk) > remaining
    decoded = captured.decode('utf-8', 'replace')
    encoded = decoded.encode('utf-8')
    if len(encoded) > limit:
        decoded = encoded[:limit].decode('utf-8', 'ignore')
        truncated = True
    return decoded, truncated


async def _wait_direct(process):
    # asyncio's transport wait may also wait for inherited pipe handles. Poll
    # the direct exit independently, then settle the whole owned group below.
    while process.returncode is None:
        await asyncio.sleep(.01)


async def _cleanup(process, readers, grace):
    try:
        if os.name == 'nt':
            await _terminate_windows(process)
        else:
            await _terminate_posix(process, grace)
        await asyncio.wait_for(process.wait(), 4)
        return await asyncio.wait_for(asyncio.gather(*readers), 4)
    except BaseException as error:
        # Best effort escalation does not turn an observation failure into a
        # successful stop. The Host must retain its executing/profile lock.
        if os.name != 'nt':
            _signal_group(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            process.kill()
        try:
            await asyncio.wait_for(process.wait(), 4)
        except (OSError, TimeoutError):
            pass
        for reader in readers:
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        if isinstance(error, PublicationProcessCleanupError):
            raise
        raise PublicationProcessCleanupError('发布进程终止或回收未能确认', pid=process.pid) from error


async def run_publication_process(argv, *, cwd, env, timeout, output_limit=1024 * 1024,
                                  terminate_grace=1.0, preflight=None):
    """Run fixed argv without a shell; cancel/timeout only settle after cleanup.

    stdout/stderr are bounded diagnostic text, never publication receipts. The
    caller's final authorization check can run in preflight immediately before
    spawn. A cleanup error intentionally differs from a confirmed cancellation.
    """
    if (isinstance(argv, (str, bytes)) or not isinstance(argv, (list, tuple)) or not argv
            or any(not isinstance(arg, str) or '\0' in arg for arg in argv)
            or not isinstance(env, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                or '\0' in key or '\0' in value or '=' in key for key, value in env.items())
            or type(output_limit) is not int or output_limit < 0
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
            or type(terminate_grace) not in (int, float) or not math.isfinite(terminate_grace) or terminate_grace < 0):
        raise ValueError('发布子进程参数无效')
    fixed_argv, fixed_env, fixed_cwd = tuple(argv), dict(env), os.fspath(cwd)
    if preflight is not None:
        check = preflight()
        if inspect.isawaitable(check):
            await check
    options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt'
               else {'start_new_session': True})
    launch = asyncio.create_task(asyncio.create_subprocess_exec(*fixed_argv, cwd=fixed_cwd, env=fixed_env,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **options))
    cancelled = None
    try:
        process = await asyncio.shield(launch)
    except asyncio.CancelledError as error:
        cancelled = error
        try:
            process = await _uninterruptible(launch)
        except (OSError, ValueError):
            raise cancelled
    except (OSError, ValueError) as error:
        raise PublicationProcessStartError('发布子进程无法启动') from error
    readers = [asyncio.create_task(_read_bounded(process.stdout, output_limit)),
               asyncio.create_task(_read_bounded(process.stderr, output_limit))]
    timed_out = False
    runtime_error = None
    if cancelled is None:
        try:
            await asyncio.wait_for(_wait_direct(process), timeout)
        except TimeoutError:
            timed_out = True
        except asyncio.CancelledError as error:
            cancelled = error
        except BaseException as error:
            runtime_error = error
    cleanup = asyncio.create_task(_cleanup(process, readers, terminate_grace))
    try:
        outputs = await asyncio.shield(cleanup)
    except asyncio.CancelledError as error:
        cancelled = cancelled or error
        outputs = await _uninterruptible(cleanup)
    if cancelled is not None:
        raise cancelled
    if runtime_error is not None:
        raise runtime_error
    return PublicationProcessResult(fixed_argv, process.returncode, outputs[0][0], outputs[1][0], process.pid,
                                    timed_out, outputs[0][1] or outputs[1][1])
