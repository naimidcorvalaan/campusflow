"""Tie launcher children to its lifetime, including closing the console window.

Windows Job Objects are standard OS process lifetime management. No persistent
service, system setting, shell command, or third-party dependency is installed.
"""
import os


class ChildJob:
    def __init__(self):
        self.handle = None
        if os.name != 'nt':
            return
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [('per_process_time',ctypes.c_longlong),('per_job_time',ctypes.c_longlong),
                ('flags',wintypes.DWORD),('min_working_set',ctypes.c_size_t),
                ('max_working_set',ctypes.c_size_t),('active_process_limit',wintypes.DWORD),
                ('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]

        class IOCounters(ctypes.Structure):
            _fields_ = [(name,ctypes.c_ulonglong) for name in
                ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic',BasicLimits),('io',IOCounters),('process_memory',ctypes.c_size_t),
                ('job_memory',ctypes.c_size_t),('peak_process_memory',ctypes.c_size_t),
                ('peak_job_memory',ctypes.c_size_t)]

        self.api = ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p,wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE,wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None,None)
        if not self.handle:
            raise OSError('Cannot create child process lifetime group')
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            self.close()
            raise OSError('Cannot configure child process lifetime group')

    def attach(self, process):
        if self.handle and not self.api.AssignProcessToJobObject(self.handle,int(process._handle)):
            process.terminate()
            process.wait(timeout=10)
            raise OSError('Cannot attach child process lifetime group')

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
