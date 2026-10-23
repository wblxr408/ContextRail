"""Exact host process identity. Called only for explicitly registered actions."""
import os
from pathlib import Path
import signal
import subprocess


def _windows_handle(pid):
    import ctypes
    from ctypes import wintypes as w
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
    k.CloseHandle.argtypes=[w.HANDLE];k.CloseHandle.restype=w.BOOL
    k.GetProcessTimes.argtypes=[w.HANDLE,*([ctypes.POINTER(w.FILETIME)]*4)];k.GetProcessTimes.restype=w.BOOL
    k.GetExitCodeProcess.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD)];k.GetExitCodeProcess.restype=w.BOOL
    k.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];k.WaitForSingleObject.restype=w.DWORD
    h=k.OpenProcess(0x1000|0x100000,False,pid)
    if not h:return k,None,None
    a,b,c,d=w.FILETIME(),w.FILETIME(),w.FILETIME(),w.FILETIME()
    if not k.GetProcessTimes(h,*[ctypes.byref(t) for t in (a,b,c,d)]):k.CloseHandle(h);return k,None,None
    return k,h,str((a.dwHighDateTime<<32)|a.dwLowDateTime)


def inspect_process(pid):
    if type(pid) is not int or not 0<pid<2**32:return {'state':'unknown'}
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes as w
        k,h,created=_windows_handle(pid)
        if not h:return {'state':'absent' if ctypes.get_last_error()==87 else 'not_observable','pid':pid}
        try:
            exitcode=w.DWORD()
            if not k.GetExitCodeProcess(h,ctypes.byref(exitcode)):return {'state':'unknown','pid':pid}
            wait=k.WaitForSingleObject(h,0)
            return {'pid':pid,'created':created,'state':'running' if wait==258 else 'stopped' if wait==0 else 'unknown'}
        finally:k.CloseHandle(h)
    try:
        fields=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()
        return {'pid':pid,'created':fields[19],'state':'stopped' if fields[0]=='Z' else 'running'}
    except (OSError,IndexError):return {'state':'not_observable','pid':pid}


def cancel_tree(handle):
    pid=handle['pid']
    if pid in {os.getpid(),os.getppid()}:return {'status':'control_refused'}
    observed=inspect_process(pid)
    if observed.get('state')=='absent':return {'status':'stopped_outcome_unknown'}
    if not observed.get('created'):return {'status':'cancel_requested','reason':'process_not_observable'}
    if observed.get('created')!=handle.get('created'):return {'status':'identity_mismatch'}
    if observed['state']!='running':return {'status':'stopped_outcome_unknown'}
    if os.name=='nt':
        import ctypes
        k,h,created=_windows_handle(pid)
        if not h or created!=handle['created']:
            if h:k.CloseHandle(h)
            return {'status':'identity_mismatch'}
        try:
            buffer=ctypes.create_unicode_buffer(32768)
            k.GetSystemDirectoryW(buffer,len(buffer))
            try:result=subprocess.run([str(Path(buffer.value)/'taskkill.exe'),'/PID',str(pid),'/T','/F'],capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except subprocess.TimeoutExpired:return {'status':'cancel_requested','reason':'native_cancel_deadline'}
            return {'status':'cancelled' if result.returncode==0 and inspect_process(pid).get('state')!='running' else 'cancel_requested'}
        finally:k.CloseHandle(h)
    if handle.get('process_group') and os.getpgid(pid)==pid:
        os.killpg(pid,signal.SIGKILL);return {'status':'cancelled'}
    return {'status':'control_refused'}
