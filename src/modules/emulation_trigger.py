import os
import shutil
import subprocess
import logging
import ctypes
import time
import hid
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger("fhds.emulation_trigger")

_trigger_proc = None
_job_handle = None

# Windows Job Object Structures
if os.name == "nt":
    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64)
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_void_p),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD)
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t)
        ]

def start_trigger(settings) -> None:
    global _trigger_proc, _job_handle
    if _trigger_proc is not None:
        return
    if not settings.enable_emulation_trigger:
        return
    if os.name != "nt":
        return

    try:
        cmd_path = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32" / "cmd.exe"
        if not cmd_path.exists():
            log.warning("System cmd.exe not found; cannot start DualSense emulation trigger")
            return

        data_dir = Path(__file__).resolve().parent.parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        target_path = data_dir / "MilesMorales.exe"

        # Copy cmd.exe to MilesMorales.exe if missing or size differs
        if not target_path.exists() or target_path.stat().st_size != cmd_path.stat().st_size:
            shutil.copy2(cmd_path, target_path)

        # Create Job Object to ensure child dies if parent is killed/crashes
        try:
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
            JobObjectExtendedLimitInformation = 9

            _job_handle = ctypes.windll.kernel32.CreateJobObjectW(None, None)
            if _job_handle:
                info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

                res = ctypes.windll.kernel32.SetInformationJobObject(
                    _job_handle,
                    JobObjectExtendedLimitInformation,
                    ctypes.byref(info),
                    ctypes.sizeof(info)
                )
                if not res:
                    log.warning("SetInformationJobObject failed")
        except Exception as je:
            log.warning("Failed to create Job Object: %s", je)

        log.info("Starting background emulation trigger: MilesMorales.exe...")
        _trigger_proc = subprocess.Popen(
            [str(target_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        # Assign process to Job Object
        if _job_handle and _trigger_proc:
            try:
                h_process = _trigger_proc._handle
                res = ctypes.windll.kernel32.AssignProcessToJobObject(_job_handle, h_process)
                if not res:
                    log.warning("AssignProcessToJobObject failed")
            except Exception as ae:
                log.warning("Failed to assign process to Job Object: %s", ae)

    except Exception as e:
        log.warning("Failed to start DualSense emulation trigger: %s", e)

def stop_trigger() -> None:
    global _trigger_proc, _job_handle
    if _trigger_proc is not None:
        try:
            log.info("Stopping background emulation trigger...")
            _trigger_proc.terminate()
            _trigger_proc.wait(timeout=1.0)
        except Exception:
            try:
                _trigger_proc.kill()
            except Exception:
                pass
        finally:
            _trigger_proc = None

    if _job_handle is not None:
        try:
            ctypes.windll.kernel32.CloseHandle(_job_handle)
        except Exception:
            pass
        finally:
            _job_handle = None
