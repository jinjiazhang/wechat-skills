"""Standalone Windows UI transport. OCR is local; no model/API is called."""
from contextlib import contextmanager
import ctypes
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid

from .chat_state import ChatError


def compact(text):
    return re.sub(r'\s+', '', text)


class WindowsSender:
    def __init__(self):
        ctypes.windll.user32.SetProcessDPIAware()
        import win32gui, win32con, win32api, win32process
        import pyperclip
        self.gui, self.con, self.api, self.proc, self.clip = win32gui, win32con, win32api, win32process, pyperclip
        ctypes.windll.user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        ctypes.windll.user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        self.hwnd = None
        self.searching = False
        self.submitted = False

    def select(self):
        windows = []
        def collect(hwnd, unused):
            rect = self.gui.GetWindowRect(hwnd)
            if rect[2]-rect[0] >= 600 and rect[3]-rect[1] >= 400 and self.gui.GetClassName(hwnd) in ('Qt51514QWindowIcon', 'WeChatMainWndForPC') and self.gui.GetWindowText(hwnd) in ('微信', 'WeChat', 'Weixin'):
                windows.append(hwnd)
        self.gui.EnumWindows(collect, None)
        if len(windows) != 1:
            raise ChatError('UI_WINDOW_AMBIGUOUS', 'Exactly one WeChat main window is required')
        self.hwnd = windows[0]
        _, pid = self.proc.GetWindowThreadProcessId(self.hwnd)
        self.pid = pid
        handle = self.api.OpenProcess(0x1000, False, pid)
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = ctypes.c_ulong(len(buffer))
            if not ctypes.windll.kernel32.QueryFullProcessImageNameW(ctypes.c_void_p(int(handle)), 0, buffer, ctypes.byref(size)):
                raise ChatError('UI_PROCESS_UNKNOWN', 'Cannot verify the window process')
            process_path = buffer.value
        finally:
            handle.Close()
        if Path(process_path).name.lower() not in ('weixin.exe', 'wechat.exe'):
            raise ChatError('UI_WRONG_PROCESS', 'Window is not owned by WeChat')
        self.gui.ShowWindow(self.hwnd, self.con.SW_RESTORE)
        was_topmost = bool(self.gui.GetWindowLong(self.hwnd, self.con.GWL_EXSTYLE) & self.con.WS_EX_TOPMOST)
        try:
            self.gui.SetWindowPos(self.hwnd, self.con.HWND_TOPMOST, 0, 0, 0, 0,
                                  self.con.SWP_NOMOVE | self.con.SWP_NOSIZE)
            self.gui.SetForegroundWindow(self.hwnd)
        except Exception:
            current_thread = self.api.GetCurrentThreadId()
            foreground_thread = self.proc.GetWindowThreadProcessId(self.gui.GetForegroundWindow())[0]
            attached = False
            try:
                if current_thread != foreground_thread:
                    self.proc.AttachThreadInput(current_thread, foreground_thread, True)
                    attached = True
                self.gui.SetForegroundWindow(self.hwnd)
            except Exception:
                raise ChatError('UI_FOCUS_FAILED', 'Bring WeChat to the foreground and retry after checking the outbox') from None
            finally:
                if attached:
                    self.proc.AttachThreadInput(current_thread, foreground_thread, False)
        finally:
            if not was_topmost:
                self.gui.SetWindowPos(self.hwnd, self.con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                      self.con.SWP_NOMOVE | self.con.SWP_NOSIZE | self.con.SWP_NOACTIVATE)
        time.sleep(.3)
        self.guard()

    def guard(self, rect=None):
        foreground = self.gui.GetForegroundWindow()
        popup = self.searching and self.proc.GetWindowThreadProcessId(foreground)[1] == self.pid
        if foreground != self.hwnd and not popup:
            raise ChatError('UI_FOCUS_CHANGED', 'Foreground window changed; stopped without retry')
        if rect is not None and self.gui.GetWindowRect(self.hwnd) != rect:
            raise ChatError('UI_WINDOW_MOVED', 'WeChat window moved; stopped without retry')

    def keys(self, *keys):
        self.guard()
        for key in keys:
            self.api.keybd_event(key, 0, 0, 0)
            time.sleep(.035)
        for key in reversed(keys):
            self.api.keybd_event(key, 0, self.con.KEYEVENTF_KEYUP, 0)
            time.sleep(.035)
        time.sleep(.15)

    def paste(self, text):
        self.clip.copy(text)
        self.keys(self.con.VK_CONTROL, ord('V'))

    def click(self, rect, x, y):
        self.guard(rect)
        point = (rect[0] + int(x), rect[1] + int(y))
        control = self.gui.WindowFromPoint(point)
        if self.gui.GetAncestor(control, 2) != self.hwnd:
            raise ChatError('UI_OCCLUDED', 'Click point is covered by another window')
        self.api.SetCursorPos(point)
        self.api.mouse_event(self.con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        self.api.mouse_event(self.con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(.15)

    def observe(self):
        from PIL import ImageGrab
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        self.guard()
        rect = self.gui.GetWindowRect(self.hwnd)
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        if width < 700 or height < 500:
            raise ChatError('UI_LAYOUT_UNSUPPORTED', 'WeChat main window is too small')
        with tempfile.TemporaryDirectory(prefix='wechat-ocr-') as folder:
            image_path = Path(folder) / 'window.png'
            self.last_image = ImageGrab.grab(bbox=rect, all_screens=True).convert('RGB')
            self.last_image.save(image_path)
            command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                       '-File', str(Path(__file__).with_name('windows_ocr.ps1')), '-ImagePath', str(image_path)]
            result = subprocess.run(command, capture_output=True, timeout=30,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise ChatError('OCR_UNAVAILABLE', 'Windows OCR failed; check installed OCR languages')
            lines = json.loads(result.stdout.decode('utf-8-sig'))['lines']
        self.guard(rect)
        return rect, width, height, lines

    @staticmethod
    def title_matches(name, width, height, lines):
        for line in lines:
            words = line.get('words', [])
            if words and min(w['x'] for w in words) > width * .25 and max(w['y'] + w['height'] for w in words) < height * .13:
                text = compact(line['text'])
                if text == compact(name) or re.sub(r'[（(]\d+[）)]$', '', text) == compact(name):
                    return True
        return False

    def checked_title(self, name):
        observation = self.observe()
        if not self.title_matches(name, *observation[1:]):
            raise ChatError('UI_TARGET_MISMATCH', 'OCR title does not exactly match the selected conversation')
        return observation

    @contextmanager
    def lock(self):
        import win32event, win32api
        mutex = win32event.CreateMutex(None, False, 'Local\\WeChatSkillsSend')
        acquired = False
        try:
            status = win32event.WaitForSingleObject(mutex, 0)
            if status not in (0, 0x80):
                raise ChatError('UI_BUSY', 'Another WeChat sender is active')
            acquired = True
            yield
        finally:
            if acquired:
                win32event.ReleaseMutex(mutex)
            win32api.CloseHandle(mutex)

    def send(self, name, text):
        with self.lock():
            self.select()
            old_clipboard = self.clip.paste()
            try:
                rect, width, height, lines = self.observe()
                if not self.title_matches(name, width, height, lines):
                    fields = [w for line in lines for w in line.get('words', [])
                              if w['text'] in ('搜', '搜索', 'Search') and w['x'] < width*.28 and w['y'] < height*.13]
                    if len(fields) != 1:
                        raise ChatError('UI_SEARCH_MISSING', 'Cannot uniquely identify the search box')
                    field = fields[0]
                    self.click(rect, field['x']+field['width']/2, field['y']+field['height']/2)
                    self.searching = True
                    self.keys(self.con.VK_CONTROL, ord('A'))
                    self.paste(name)
                    self.keys(self.con.VK_CONTROL, ord('A'))
                    self.clip.copy(str(uuid.uuid4()))
                    self.keys(self.con.VK_CONTROL, ord('C'))
                    if self.clip.paste() != name:
                        raise ChatError('UI_SEARCH_MISMATCH', 'Search field does not contain the intended target')
                    time.sleep(.8)
                    self.keys(self.con.VK_RETURN)
                    time.sleep(.6)
                    self.searching = False
                rect, width, height, lines = self.checked_title(name)
                # Main-window editor occupies the bottom right; verify its contents before sending.
                self.click(rect, width * .55, height * .86)
                self.keys(self.con.VK_CONTROL, ord('A'))
                sentinel = str(uuid.uuid4())
                self.clip.copy(sentinel)
                self.keys(self.con.VK_CONTROL, ord('C'))
                draft = self.clip.paste()
                if draft.replace('\r\n', '\n') not in ('', sentinel, text):
                    raise ChatError('UI_EXISTING_DRAFT', 'The editor already contains a draft; preserve it and stop')
                if draft.replace('\r\n', '\n') != text:
                    self.paste(text)
                self.keys(self.con.VK_CONTROL, ord('A'))
                self.clip.copy(sentinel)
                self.keys(self.con.VK_CONTROL, ord('C'))
                if self.clip.paste().replace('\r\n', '\n') != text:
                    raise ChatError('UI_TEXT_MISMATCH', 'Editor content did not match the requested text')
                self.keys(self.con.VK_END)
                rect, width, height, lines = self.checked_title(name)
                buttons = []
                for line in lines:
                    words = line.get('words', [])
                    if words and compact(line['text']) in ('发送', 'Send'):
                        x, y = min(w['x'] for w in words), min(w['y'] for w in words)
                        if x > width * .8 and y > height * .85:
                            buttons.append({'x': x, 'y': y,
                                            'width': max(w['x']+w['width'] for w in words)-x,
                                            'height': max(w['y']+w['height'] for w in words)-y})
                if not buttons:
                    # WeChat 4.1 white-on-green Send text is sometimes omitted by Windows OCR.
                    # Restrict the fallback to the known bottom-right button geometry.
                    from PIL import Image
                    left, top = int(width*.88), int(height*.93)
                    crop = self.last_image.crop((left, top, width, height))
                    mask = Image.new('L', crop.size)
                    mask.putdata([255 if g > 130 and r < 60 and 50 < b < 190 and g > b*1.25 else 0
                                  for r,g,b in crop.getdata()])
                    box = mask.getbbox()
                    if box:
                        x1,y1,x2,y2 = box
                        bw,bh = x2-x1,y2-y1
                        fill = sum(1 for v in mask.crop(box).getdata() if v)/(bw*bh)
                        if .035*width < bw < .09*width and .018*height < bh < .05*height and 1.8 < bw/bh < 3.5 and fill > .65:
                            buttons.append({'x': left+x1, 'y': top+y1, 'width': bw, 'height': bh})
                if len(buttons) != 1:
                    raise ChatError('UI_SEND_BUTTON_MISSING', 'Cannot uniquely identify the Send button')
                button = buttons[0]
                self.keys(self.con.VK_CONTROL, ord('A'))
                self.clip.copy(sentinel)
                self.keys(self.con.VK_CONTROL, ord('C'))
                if self.clip.paste().replace('\r\n', '\n') != text:
                    raise ChatError('UI_TEXT_MISMATCH', 'Editor changed before Send; stopped')
                self.submitted = True
                self.click(rect, button['x'] + button['width']/2, button['y'] + button['height']/2)
            finally:
                self.clip.copy(old_clipboard)

    def inspect(self):
        self.select()
        rect, width, height, lines = self.observe()
        return {'rect': rect, 'width': width, 'height': height, 'lines': lines}
