
import ctypes, ctypes.wintypes, time, sys
from PIL import ImageGrab
import win32gui, win32con
import pyautogui

sys.stdout = open('C:/production scheduler/_out2.txt', 'w', encoding='utf-8')

def find_app():
    result = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            if win32gui.GetWindowText(hwnd) == 'Production Planner':
                result.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return result[0] if result else None

hwnd = find_app()
print(f'hwnd={hwnd}')
if not hwnd:
    sys.stdout.close(); sys.exit(1)

win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
win32gui.SetForegroundWindow(hwnd)
time.sleep(1.5)

r = win32gui.GetWindowRect(hwnd)
win_left, win_top, win_right, win_bottom = r
print(f'rect={r}')

# From the maximized screenshot, toolbar row 2 (action buttons) is at
# approx window-relative y=65. Summary button is near the right end.
# Window starts at (-9,-9) screen coords due to maximize border offset.
# Screen coords: toolbar_y = win_top + 65, summary_x = win_right - 220
toolbar_y_screen = win_top + 65
summary_x_screen = win_right - 220

print(f'Summary button target: screen ({summary_x_screen}, {toolbar_y_screen})')

# Make sure app is foreground before clicking
pyautogui.moveTo(summary_x_screen, toolbar_y_screen, duration=0.3)
time.sleep(0.3)
win32gui.SetForegroundWindow(hwnd)
time.sleep(0.3)
pyautogui.click(summary_x_screen, toolbar_y_screen)
time.sleep(1.5)

# Screenshot with app still foreground
win32gui.SetForegroundWindow(hwnd)
time.sleep(0.3)
img = ImageGrab.grab(all_screens=True)
crop = img.crop(r)
crop.save('C:/production scheduler/test_sum1.png')
print('Shot 1 saved')

# Click again to toggle back off
pyautogui.click(summary_x_screen, toolbar_y_screen)
time.sleep(1.0)
win32gui.SetForegroundWindow(hwnd)
time.sleep(0.3)
img2 = ImageGrab.grab(all_screens=True)
crop2 = img2.crop(r)
crop2.save('C:/production scheduler/test_sum_off.png')
print('Shot off saved')

sys.stdout.close()
