"""Windows HTTPS fallback using WinHTTP and the OS certificate store."""
import ctypes
from ctypes import wintypes as w
import urllib.error
import urllib.parse


def api():
    dll = ctypes.WinDLL('winhttp', use_last_error=True)
    handle = w.HANDLE
    signatures = {
        'WinHttpOpen': (handle, [w.LPCWSTR,w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD]),
        'WinHttpConnect': (handle, [handle,w.LPCWSTR,w.WORD,w.DWORD]),
        'WinHttpOpenRequest': (handle,[handle,w.LPCWSTR,w.LPCWSTR,w.LPCWSTR,w.LPCWSTR,ctypes.c_void_p,w.DWORD]),
        'WinHttpSetTimeouts': (w.BOOL,[handle,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int]),
        'WinHttpSetOption': (w.BOOL,[handle,w.DWORD,ctypes.c_void_p,w.DWORD]),
        'WinHttpSendRequest': (w.BOOL,[handle,w.LPCWSTR,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,ctypes.c_size_t]),
        'WinHttpReceiveResponse': (w.BOOL,[handle,ctypes.c_void_p]),
        'WinHttpQueryHeaders': (w.BOOL,[handle,w.DWORD,w.LPCWSTR,ctypes.c_void_p,ctypes.POINTER(w.DWORD),ctypes.POINTER(w.DWORD)]),
        'WinHttpReadData': (w.BOOL,[handle,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]),
        'WinHttpCloseHandle': (w.BOOL,[handle]),
    }
    for name,(result,args) in signatures.items():
        fn=getattr(dll,name);fn.restype=result;fn.argtypes=args
    return dll


def checked(value):
    if not value:
        raise OSError(ctypes.get_last_error(), 'Windows HTTPS connection failed')
    return value


class Response:
    def __init__(self, dll, handles):
        self.dll=dll;self.handles=handles;self.request=handles[-1]
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
    def close(self):
        for handle in reversed(self.handles):self.dll.WinHttpCloseHandle(handle)
        self.handles=[]
    def read(self,size):
        result=bytearray()
        while len(result)<size:
            length=min(128*1024,size-len(result));buffer=ctypes.create_string_buffer(length);received=w.DWORD()
            checked(self.dll.WinHttpReadData(self.request,buffer,length,ctypes.byref(received)))
            if not received.value:break
            result.extend(buffer.raw[:received.value])
        return bytes(result)


def open_native(url, validate_url):
    dll=api()
    for _ in range(6):
        validate_url(url);parsed=urllib.parse.urlsplit(url);handles=[]
        try:
            # This fallback is process-local direct access, without changing system proxy settings.
            session=checked(dll.WinHttpOpen('MarkFlow-Updater',1,None,None,0));handles.append(session)
            checked(dll.WinHttpSetTimeouts(session,10000,20000,20000,20000))
            connection=checked(dll.WinHttpConnect(session,parsed.hostname,443,0));handles.append(connection)
            path=parsed.path or '/'
            if parsed.query:path+='?'+parsed.query
            request=checked(dll.WinHttpOpenRequest(connection,'GET',path,None,None,None,0x00800000));handles.append(request)
            # Disable automatic redirects so every destination is validated before contact.
            policy=w.DWORD(0)
            checked(dll.WinHttpSetOption(request,88,ctypes.byref(policy),ctypes.sizeof(policy)))
            checked(dll.WinHttpSendRequest(request,'Accept: application/octet-stream\r\n',0xffffffff,None,0,0,0))
            checked(dll.WinHttpReceiveResponse(request,None))
            status=w.DWORD();length=w.DWORD(ctypes.sizeof(status))
            checked(dll.WinHttpQueryHeaders(request,19|0x20000000,None,ctypes.byref(status),ctypes.byref(length),None))
            response=Response(dll,handles)
            if status.value in (301,302,303,307,308):
                length=w.DWORD()
                dll.WinHttpQueryHeaders(request,33,None,None,ctypes.byref(length),None)
                if not 0<length.value<32768:raise ValueError('GitHub 重定向无效')
                buffer=ctypes.create_unicode_buffer(length.value//2+1)
                checked(dll.WinHttpQueryHeaders(request,33,None,buffer,ctypes.byref(length),None))
                next_url=urllib.parse.urljoin(url,buffer.value)
                validate_url(next_url)
                response.close();handles=[];url=next_url;continue
            if not 200<=status.value<300:
                raise urllib.error.HTTPError(url,status.value,'GitHub request failed',{},None)
            return response
        except Exception:
            for handle in reversed(handles):dll.WinHttpCloseHandle(handle)
            raise
    raise ValueError('GitHub 重定向次数过多')
