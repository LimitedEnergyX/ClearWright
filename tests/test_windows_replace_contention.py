"""Empirical Windows os.replace sharing semantics (medglitch #3, test-gated).

Establishes, on the host where the fix runs, the OS behaviour the writer-side
retry (#1) defends against, and records the reader-helper determination:

  1. A destination held open by a PLAIN open() makes os.replace fail with
     OSError winerror 5 or 32.
  2. Holding the destination open with FILE_SHARE_DELETE does NOT let
     os.replace (MoveFileExW REPLACE_EXISTING) succeed either: the destination
     name stays occupied until every handle closes, so the rename-over still
     fails winerror 5/32.

Because (2) holds, a shared FILE_SHARE_DELETE reader helper would NOT enable the
writer's os.replace while a reader is open. Per the approved plan, the reader
helper is therefore NOT adopted and the server's readers are left unchanged; the
writer-side retry in _atomic_write_json is the mitigation. If a future host lets
(2) succeed, this test fails and the reader-helper decision must be revisited.
Skipped off Windows (POSIX rename never exhibits the contention).
"""
import os
import shutil
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt", "Windows-only os.replace sharing semantics")
class WindowsReplaceContentionTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="cw-winreplace-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.dst = os.path.join(self.d, "dst.json")
        self.src = os.path.join(self.d, "src.json")
        with open(self.dst, "w", encoding="utf-8") as fh:
            fh.write('{"v": "old"}')
        with open(self.src, "w", encoding="utf-8") as fh:
            fh.write('{"v": "new"}')

    def test_plain_open_reader_blocks_replace(self):
        reader = open(self.dst, "r", encoding="utf-8")  # no FILE_SHARE_DELETE
        try:
            with self.assertRaises(OSError) as cm:
                os.replace(self.src, self.dst)
            self.assertIn(getattr(cm.exception, "winerror", None), (5, 32))
        finally:
            reader.close()

    def test_share_delete_reader_still_blocks_replace(self):
        import ctypes
        from ctypes import wintypes

        GENERIC_READ = 0x80000000
        FILE_SHARE_READ = 0x1
        FILE_SHARE_WRITE = 0x2
        FILE_SHARE_DELETE = 0x4
        OPEN_EXISTING = 3
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        CreateFileW = kernel32.CreateFileW
        CreateFileW.restype = wintypes.HANDLE
        CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                wintypes.HANDLE]
        CloseHandle = kernel32.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]

        handle = CreateFileW(
            self.dst, GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None, OPEN_EXISTING, 0, None)
        self.assertNotEqual(handle, INVALID_HANDLE_VALUE,
                            "CreateFileW failed: %d" % ctypes.get_last_error())
        try:
            # Determination: even a FILE_SHARE_DELETE handle does not free the
            # destination name, so the rename-over still fails on this host ->
            # readers are left unchanged (see module docstring).
            with self.assertRaises(OSError) as cm:
                os.replace(self.src, self.dst)
            self.assertIn(getattr(cm.exception, "winerror", None), (5, 32))
        finally:
            CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()
