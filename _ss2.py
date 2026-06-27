
import ctypes, ctypes.wintypes, time, sys
from PIL import ImageGrab

sys.stdout = open('C:/production scheduler/_ss_out.txt', 'w', encoding='utf-8')

user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))

hwnd_found = []
def enum_cb(hwnd, lParam):
    if user32.IsWindowVisible(hwnd):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            print(f'WIN: {title}')
            if 'lanner' in title or 'roduction' in title:
                hwnd_found.append((hwnd, title))
    return True

user32.EnumWindows(EnumWindowsProc(enum_cb), 0)
print(f'Found: {[t for _,t in hwnd_found]}')

if hwnd_found:
    hwnd = hwnd_found[0][0]
    user32.SetWindowPos(hwnd, 0, 50, 50, 1440, 900, 0x0040)
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.0)
    rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    img = ImageGrab.grab(all_screens=True)
    crop = img.crop((rect.left, rect.top, rect.right, rect.bottom))
    crop.save('C:/production scheduler/screenshot_summary.png')
    print('Saved')
sys.stdout.close()
