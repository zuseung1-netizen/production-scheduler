
import ctypes, ctypes.wintypes, time
from PIL import ImageGrab

user32 = ctypes.windll.user32
EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
GetWindowText = user32.GetWindowTextW
GetWindowTextLength = user32.GetWindowTextLengthW
IsWindowVisible = user32.IsWindowVisible
SetForegroundWindow = user32.SetForegroundWindow
SetWindowPos = user32.SetWindowPos

hwnd_found = []
def enum_cb(hwnd, lParam):
    if IsWindowVisible(hwnd):
        length = GetWindowTextLength(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            GetWindowText(hwnd, buf, length + 1)
            title = buf.value
            if "Production" in title or "Planner" in title:
                hwnd_found.append((hwnd, title))
    return True

EnumWindows(EnumWindowsProc(enum_cb), 0)
print("Found:", [t for _,t in hwnd_found])

if hwnd_found:
    hwnd = hwnd_found[0][0]
    SetWindowPos(hwnd, 0, 50, 50, 1400, 900, 0x0040)
    SetForegroundWindow(hwnd)
    time.sleep(0.8)
    rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    print(f"Rect: {rect.left},{rect.top},{rect.right},{rect.bottom}")
    img = ImageGrab.grab(all_screens=True)
    crop = img.crop((rect.left, rect.top, rect.right, rect.bottom))
    crop.save("screenshot_startup.png")
    print("Saved screenshot_startup.png")
