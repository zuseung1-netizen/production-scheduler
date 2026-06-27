
import ctypes, ctypes.wintypes, time, sys
from PIL import ImageGrab
import win32gui, win32con, win32api

sys.stdout = open('C:/production scheduler/_out.txt', 'w', encoding='utf-8')

def find_app():
    result = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if 'Production Planner' == t:
                result.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return result[0] if result else None

hwnd = find_app()
print(f'hwnd={hwnd}')
if hwnd:
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, 50, 50, 1500, 950, 0)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(1.0)
    r = win32gui.GetWindowRect(hwnd)
    img = ImageGrab.grab(all_screens=True)
    crop = img.crop(r)
    crop.save('C:/production scheduler/test_detail.png')
    print(f'Detail shot saved rect={r}')
sys.stdout.close()
