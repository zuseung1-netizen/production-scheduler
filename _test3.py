
import time, sys, ctypes, ctypes.wintypes
from PIL import ImageGrab
import win32gui, win32con, win32api
import pyautogui

out = open('C:/production scheduler/_out3.txt', 'w', encoding='utf-8')

def log(s): out.write(s+'\n'); out.flush()

def find_app():
    result = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            if win32gui.GetWindowText(hwnd) == 'Production Planner':
                result.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return result[0] if result else None

hwnd = find_app()
log(f'hwnd={hwnd}')
if not hwnd:
    out.close(); sys.exit(1)

# Maximize and bring to front
win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
time.sleep(0.5)
win32gui.BringWindowToTop(hwnd)
win32gui.SetForegroundWindow(hwnd)
time.sleep(1.5)

r = win32gui.GetWindowRect(hwnd)
win_left, win_top, win_right, win_bottom = r
log(f'rect={r}')

# Take a cropped toolbar screenshot to find button positions
img = ImageGrab.grab(all_screens=True)
full = img.crop(r)
# Save just the top 120px (toolbar area)
toolbar_crop = full.crop((0, 0, full.width, 120))
toolbar_crop.save('C:/production scheduler/test_toolbar.png')
log('Toolbar strip saved')

# Based on maximized screenshot, the second toolbar row is at win_relative ~60-90
# Action buttons row: right side has Summary btn
# Let us scan across the top area for the Summary button
# Window is ~1938px wide, Summary btn text is Summary
# From detail_max.png: visible Summary text at roughly x=1700 in window
# Screen coords: win_left + 1700 = -9 + 1700 = 1691

for try_y in [65, 70, 75, 80]:
    for try_x_offset in [220, 240, 260, 200]:
        screen_x = win_right - try_x_offset
        screen_y = win_top + try_y
        log(f'Try click ({screen_x},{screen_y})')
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        pyautogui.click(screen_x, screen_y)
        time.sleep(0.5)
        # Check if Summary is now on by taking screenshot
        cur = win32gui.GetForegroundWindow()
        if win32gui.GetWindowText(cur) == 'Production Planner':
            log(f'App still focused after click at ({screen_x},{screen_y})')
            img2 = ImageGrab.grab(all_screens=True)
            c2 = img2.crop(r)
            c2.save(f'C:/production scheduler/test_click_{try_y}_{try_x_offset}.png')
            break
    break

out.close()
