import os
import sys
import json
import re
import time
import threading
import subprocess
import ctypes
from ctypes import wintypes
import tkinter as tk
from PIL import Image, ImageDraw, ImageFont, ImageTk
import customtkinter as ctk
import pystray
import winreg
import urllib.request
import urllib.error

VERSION = "1.0.0"


def is_newer_version(latest, current):
    try:
        latest_parts = [int(x) for x in re.findall(r'\d+', latest)]
        current_parts = [int(x) for x in re.findall(r'\d+', current)]
        max_len = max(len(latest_parts), len(current_parts))
        latest_parts += [0] * (max_len - len(latest_parts))
        current_parts += [0] * (max_len - len(current_parts))
        return latest_parts > current_parts
    except Exception:
        return latest != current

# ==============================================================================
# WINDOWS API DECLARATIONS
# ==============================================================================

# Structure for Win32 margins (for shadows)
class MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]

# Structure for Gamma Ramp
class RAMP(ctypes.Structure):
    _fields_ = [("red", ctypes.c_ushort * 256),
                ("green", ctypes.c_ushort * 256),
                ("blue", ctypes.c_ushort * 256)]

# Structure for System Power Status
class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ('ACLineStatus', ctypes.c_byte),
        ('BatteryFlag', ctypes.c_byte),
        ('BatteryLifePercent', ctypes.c_byte),
        ('SystemStatusFlag', ctypes.c_byte),
        ('BatteryLifeTime', ctypes.c_ulong),
        ('BatteryFullLifeTime', ctypes.c_ulong),
    ]

# Win32 Consts
GWL_WNDPROC = -4
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040

# Subclass WndProc callback signature
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_int64, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM)

# Signature resolution for 32/64-bit Get/SetWindowLongPtr
try:
    SetWindowLongPtr = ctypes.windll.user32.SetWindowLongPtrW
    SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    SetWindowLongPtr.restype = ctypes.c_void_p
    
    GetWindowLongPtr = ctypes.windll.user32.GetWindowLongPtrW
    GetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int]
    GetWindowLongPtr.restype = ctypes.c_void_p
except AttributeError:
    SetWindowLongPtr = ctypes.windll.user32.SetWindowLongW
    SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    SetWindowLongPtr.restype = ctypes.c_long
    
    GetWindowLongPtr = ctypes.windll.user32.GetWindowLongW
    GetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int]
    GetWindowLongPtr.restype = ctypes.c_long

CallWindowProc = ctypes.windll.user32.CallWindowProcW
CallWindowProc.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
CallWindowProc.restype = ctypes.c_int64

# Global WndProc Hook Variables
_wndproc_callback_ref = None
_old_wndproc_ptr = None
_block_shutdown_active = False

def my_wnd_proc(hwnd, msg, wparam, lparam):
    global _block_shutdown_active, _old_wndproc_ptr
    if msg == WM_QUERYENDSESSION:
        if _block_shutdown_active:
            # Return 0 (FALSE) to block the shutdown/restart
            return 0
    return CallWindowProc(_old_wndproc_ptr, hwnd, msg, wparam, lparam)

# ==============================================================================
# APPLICATION PATHS & STATE DATA
# ==============================================================================
def get_asset_path(filename):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

def get_persistent_path(filename):
    appdata = os.getenv('LOCALAPPDATA')
    if appdata:
        dir_path = os.path.join(appdata, 'AstroMode')
        os.makedirs(dir_path, exist_ok=True)
        return os.path.join(dir_path, filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

RESTORE_PATH = get_persistent_path("restore_state.json")
ICON_PATH = get_asset_path("astro_icon.png")
BANNER_PATH = get_asset_path("gaia_pillars.jpg")

# ==============================================================================
# CONFIGURATION MANAGER & USER PREFERENCES
# ==============================================================================
CONFIG_PATH = get_persistent_path("config.json")

class AstroConfig:
    def __init__(self):
        self.data = {
            "autolaunch": False,
            "last_x": None,
            "last_y": None,
            "red_filter_enabled": True,
            "red_filter_intensity": 1.0,
            "dim_display_enabled": True,
            "dim_display_target": 0,
            "cpu_limit_enabled": True,
            "cpu_limit_max": 50,
            "never_sleep_enabled": True,
            "prevent_shutdown_enabled": True,
            "display_timeout_enabled": True,
            "display_timeout_value": "Never",
            "usb_power_enabled": True,
            "lid_close_enabled": True
        }
        self.load()

    def load(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'r') as f:
                    loaded = json.load(f)
                    self.data.update(loaded)
            except Exception as e:
                print(f"Error loading config: {e}")

    def save(self):
        try:
            with open(CONFIG_PATH, 'w') as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}")

config = AstroConfig()

def set_autolaunch(enabled):
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "AstroMode"
    
    if getattr(sys, 'frozen', False):
        exe_path = sys.executable
    else:
        exe_path = f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
        
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        if enabled:
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe_path)
        else:
            try:
                winreg.DeleteValue(key, app_name)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"Error setting autolaunch: {e}")
        return False

# ==============================================================================
# SETTINGS & HARDWARE CONTROL FUNCTIONS
# ==============================================================================

# 1. Power Status
def get_system_power_status():
    status = SYSTEM_POWER_STATUS()
    if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        ac_status = "Plugged In" if status.ACLineStatus == 1 else "On Battery"
        percent = status.BatteryLifePercent
        percent_str = "Unknown" if percent == 255 else f"{percent}%"
        return ac_status, percent_str
    return "Unknown", "Unknown"

# 2. Monitor Brightness (WMI/PowerShell)
def get_system_brightness():
    try:
        cmd = "powershell -Command \"(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness\""
        output = subprocess.check_output(cmd, shell=True, text=True).strip()
        return int(output)
    except Exception:
        return 50

def set_system_brightness(level):
    try:
        cmd = f"powershell -Command \"Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{ Timeout = 1; Brightness = {level} }}\""
        subprocess.run(cmd, shell=True, check=True)
        return True
    except Exception as e:
        print(f"Error setting brightness: {e}")
        return False

# 3. CPU Power Settings (powercfg)
def get_cpu_limits():
    try:
        output = subprocess.check_output("powercfg /query SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX", shell=True, text=True)
        ac_match = re.search(r"Current AC Power Setting Index:\s+(0x[0-9a-fA-F]+)", output)
        dc_match = re.search(r"Current DC Power Setting Index:\s+(0x[0-9a-fA-F]+)", output)
        ac_val = int(ac_match.group(1), 16) if ac_match else 100
        dc_val = int(dc_match.group(1), 16) if dc_match else 100
        return ac_val, dc_val
    except Exception:
        return 100, 100

def set_cpu_limits(percent):
    try:
        subprocess.run(f"powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX {percent}", shell=True, check=True)
        subprocess.run(f"powercfg /setdcvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX {percent}", shell=True, check=True)
        subprocess.run("powercfg /setactive SCHEME_CURRENT", shell=True, check=True)
        return True
    except Exception as e:
        print(f"Error setting CPU limits: {e}")
        return False

def get_min_cpu_limits():
    try:
        output = subprocess.check_output("powercfg /query SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEVAL", shell=True, text=True)
        ac_match = re.search(r"Current AC Power Setting Index:\s+(0x[0-9a-fA-F]+)", output)
        dc_match = re.search(r"Current DC Power Setting Index:\s+(0x[0-9a-fA-F]+)", output)
        ac_val = int(ac_match.group(1), 16) if ac_match else 5
        dc_val = int(dc_match.group(1), 16) if dc_match else 5
        return ac_val, dc_val
    except Exception:
        return 5, 5

def set_min_cpu_limits(percent):
    try:
        subprocess.run(f"powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEVAL {percent}", shell=True, check=True)
        subprocess.run(f"powercfg /setdcvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEVAL {percent}", shell=True, check=True)
        subprocess.run("powercfg /setactive SCHEME_CURRENT", shell=True, check=True)
        return True
    except Exception as e:
        print(f"Error setting Min CPU limits: {e}")
        return False

# 4. Sleep Settings (powercfg)
def get_standby_timeouts():
    try:
        output = subprocess.check_output("powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE", shell=True, text=True)
        ac_match = re.search(r"Current AC Power Setting Index:\s+(0x[0-9a-fA-F]+)", output)
        dc_match = re.search(r"Current DC Power Setting Index:\s+(0x[0-9a-fA-F]+)", output)
        ac_val = int(ac_match.group(1), 16) if ac_match else 1800
        dc_val = int(dc_match.group(1), 16) if dc_match else 600
        return ac_val, dc_val
    except Exception:
        return 1800, 600

def set_standby_timeouts(ac_sec, dc_sec):
    try:
        subprocess.run(f"powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE {ac_sec}", shell=True, check=True)
        subprocess.run(f"powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE {dc_sec}", shell=True, check=True)
        subprocess.run("powercfg /setactive SCHEME_CURRENT", shell=True, check=True)
        return True
    except Exception as e:
        print(f"Error setting standby timeouts: {e}")
        return False

# 5. Hibernate Settings (powercfg)
def get_hibernate_timeouts():
    try:
        output = subprocess.check_output("powercfg /query SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE", shell=True, text=True)
        ac_match = re.search(r"Current AC Power Setting Index:\s+(0x[0-9a-fA-F]+)", output)
        dc_match = re.search(r"Current DC Power Setting Index:\s+(0x[0-9a-fA-F]+)", output)
        ac_val = int(ac_match.group(1), 16) if ac_match else 0
        dc_val = int(dc_match.group(1), 16) if dc_match else 0
        return ac_val, dc_val
    except Exception:
        return 0, 0

def set_hibernate_timeouts(ac_sec, dc_sec):
    try:
        subprocess.run(f"powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE {ac_sec}", shell=True, check=True)
        subprocess.run(f"powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE {dc_sec}", shell=True, check=True)
        subprocess.run("powercfg /setactive SCHEME_CURRENT", shell=True, check=True)
        return True
    except Exception as e:
        print(f"Error setting hibernate timeouts: {e}")
        return False

# 6. Display Idle Timeout (powercfg)
def get_display_timeouts():
    try:
        output = subprocess.check_output("powercfg /query SCHEME_CURRENT SUB_VIDEO VIDEOIDLE", shell=True, text=True)
        ac_match = re.search(r"Current AC Power Setting Index:\s+(0x[0-9a-fA-F]+)", output)
        dc_match = re.search(r"Current DC Power Setting Index:\s+(0x[0-9a-fA-F]+)", output)
        ac_val = int(ac_match.group(1), 16) if ac_match else 600
        dc_val = int(dc_match.group(1), 16) if dc_match else 300
        return ac_val, dc_val
    except Exception:
        return 600, 300

def set_display_timeouts(ac_sec, dc_sec):
    try:
        subprocess.run(f"powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE {ac_sec}", shell=True, check=True)
        subprocess.run(f"powercfg /setdcvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE {dc_sec}", shell=True, check=True)
        subprocess.run("powercfg /setactive SCHEME_CURRENT", shell=True, check=True)
        return True
    except Exception as e:
        print(f"Error setting display timeouts: {e}")
        return False

# 7. USB Selective Suspend (powercfg)
def get_usb_settings():
    try:
        output = subprocess.check_output("powercfg /query SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226", shell=True, text=True)
        ac_match = re.search(r"Current AC Power Setting Index:\s+(0x[0-9a-fA-F]+)", output)
        dc_match = re.search(r"Current DC Power Setting Index:\s+(0x[0-9a-fA-F]+)", output)
        ac_val = int(ac_match.group(1), 16) if ac_match else 1
        dc_val = int(dc_match.group(1), 16) if dc_match else 1
        return ac_val, dc_val
    except Exception:
        return 1, 1

def set_usb_settings(ac_val, dc_val):
    try:
        subprocess.run(f"powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 {ac_val}", shell=True, check=True)
        subprocess.run(f"powercfg /setdcvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 {dc_val}", shell=True, check=True)
        subprocess.run("powercfg /setactive SCHEME_CURRENT", shell=True, check=True)
        return True
    except Exception as e:
        print(f"Error setting USB selective suspend: {e}")
        return False

# 8. Lid Close Action (powercfg)
def get_lid_close_settings():
    try:
        output = subprocess.check_output("powercfg /query SCHEME_CURRENT SUB_BUTTONS LIDCLOSE", shell=True, text=True, stderr=subprocess.DEVNULL)
        ac_match = re.search(r"Current AC Power Setting Index:\s+(0x[0-9a-fA-F]+)", output)
        dc_match = re.search(r"Current DC Power Setting Index:\s+(0x[0-9a-fA-F]+)", output)
        ac_val = int(ac_match.group(1), 16) if ac_match else 1
        dc_val = int(dc_match.group(1), 16) if dc_match else 1
        return ac_val, dc_val
    except Exception:
        return None

def set_lid_close_settings(ac_val, dc_val):
    try:
        subprocess.run(f"powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDCLOSE {ac_val}", shell=True, check=True)
        subprocess.run(f"powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDCLOSE {dc_val}", shell=True, check=True)
        subprocess.run("powercfg /setactive SCHEME_CURRENT", shell=True, check=True)
        return True
    except Exception as e:
        print(f"Error setting lid close action: {e}")
        return False


# ==============================================================================
# STATE BACKUP AND CRASH RECOVERY
# ==============================================================================
class AstroStateManager:
    def __init__(self):
        self.backup_data = {}
        self.active_features = set()

    def load_backup(self):
        if os.path.exists(RESTORE_PATH):
            try:
                with open(RESTORE_PATH, 'r') as f:
                    self.backup_data = json.load(f)
                return True
            except Exception as e:
                print(f"Error loading backup file: {e}")
        return False

    def save_backup(self):
        try:
            with open(RESTORE_PATH, 'w') as f:
                json.dump(self.backup_data, f, indent=4)
        except Exception as e:
            print(f"Error writing backup file: {e}")

    def clear_backup(self):
        self.backup_data = {}
        if os.path.exists(RESTORE_PATH):
            try:
                os.remove(RESTORE_PATH)
            except Exception as e:
                print(f"Error deleting backup file: {e}")

    def save_original_value(self, key, value):
        if key not in self.backup_data:
            self.backup_data[key] = value
            self.save_backup()

    def get_original_value(self, key, default=None):
        return self.backup_data.get(key, default)

    def restore_system_settings(self):
        """Restores all system settings to original values stored in backup."""
        if not self.backup_data:
            return

        print("Restoring saved laptop configurations...")

        # 1. Restore Brightness
        orig_brightness = self.get_original_value("brightness")
        if orig_brightness is not None:
            set_system_brightness(orig_brightness)

        # 2. Restore CPU Power cap
        orig_cpu = self.get_original_value("cpu_limits")
        if orig_cpu is not None:
            set_cpu_limits(orig_cpu[0]) # Restore AC, DC will match it or follow suit
            
        orig_min_cpu = self.get_original_value("min_cpu_limits")
        if orig_min_cpu is not None:
            set_min_cpu_limits(orig_min_cpu[0])

        # 3. Restore Sleep standby timeouts
        orig_standby = self.get_original_value("standby_timeouts")
        if orig_standby is not None:
            set_standby_timeouts(orig_standby[0], orig_standby[1])

        # 4. Restore Hibernate timeouts
        orig_hibernate = self.get_original_value("hibernate_timeouts")
        if orig_hibernate is not None:
            set_hibernate_timeouts(orig_hibernate[0], orig_hibernate[1])

        # 5. Restore Display VideoIdle timeouts
        orig_display = self.get_original_value("display_timeouts")
        if orig_display is not None:
            set_display_timeouts(orig_display[0], orig_display[1])

        # 6. Restore USB selective suspend
        orig_usb = self.get_original_value("usb_selective_suspend")
        if orig_usb is not None:
            set_usb_settings(orig_usb[0], orig_usb[1])

        # 8. Restore Lid Close Action
        orig_lid = self.get_original_value("lid_close_action")
        if orig_lid is not None:
            set_lid_close_settings(orig_lid[0], orig_lid[1])

        # 7. Restore Gamma Ramp
        # Gamma ramp is restored by setting a standard linear ramp
        # because the original raw ramp bytes can be complex to save to JSON.
        # Restoring to a linear 100% ramp is standard and resets the display colors.
        restore_gamma_linear()

        # Clean up
        self.clear_backup()
        print("Restore complete.")

# Global state manager instance
state_manager = AstroStateManager()

# Helper to restore Gamma Ramp to 100% linear
def restore_gamma_linear():
    hdc = ctypes.windll.user32.GetDC(0)
    if hdc:
        linear_ramp = RAMP()
        for i in range(256):
            linear_ramp.red[i] = int(i * 256)
            linear_ramp.green[i] = int(i * 256)
            linear_ramp.blue[i] = int(i * 256)
        ctypes.windll.gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(linear_ramp))
        ctypes.windll.user32.ReleaseDC(0, hdc)


# ==============================================================================
# ASTRO APPLICATION CLASS
# ==============================================================================
class AstroModeApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Windows specifics
        self.hwnd = None
        self.original_gamma = None
        self.icon = None
        self.astro_mode_active = False
        
        # Load restore backup if it exists from previous crash
        if state_manager.load_backup():
            self.after(500, self.check_recovery)

        # UI Styling (Gaia Pillars Cosmic Palette)
        ctk.set_appearance_mode("dark")
        
        # Main Theme Colors matching ESA Gaia Pillars
        self.color_bg = "#080c14"          # Darker deep cosmic dark blue/black
        self.color_card = "#121824"        # Lighter slate blue card
        self.color_accent = "#f3704c"      # Neon copper-orange
        self.color_accent_hover = "#d6512e"# Muted copper-orange
        self.color_text_main = "#fcecdb"    # Cosmic gold/ivory
        self.color_text_muted = "#7f8b9d"   # Cosmic dust grey
        self.color_border = "#1b2333"       # Clean slate border
        
        # Configure window
        self.title("AstroMode Dashboard")
        self.geometry("380x620")
        self.overrideredirect(True) # Frameless window
        self.configure(fg_color=self.color_bg)
        self.attributes("-topmost", True) # Keep on top when active
        self.attributes("-alpha", 1.0)    # Fully solid background

        # Set window icon
        if os.path.exists(ICON_PATH):
            try:
                self.icon_img = ImageTk.PhotoImage(file=ICON_PATH)
                self.wm_iconphoto(True, self.icon_img)
            except Exception as e:
                print(f"Failed to set window icon: {e}")

        # Build UI layout
        self.create_widgets()
        
        # Get HWND and set subclass WndProc for blocking shutdown
        self.after(200, self.init_win32_hooks)
        
        # Intercept FocusOut and FocusIn to handle overlaying instead of minimizing
        self.bind("<FocusOut>", self.on_focus_out)
        self.bind("<FocusIn>", self.on_focus_in)
        
        # Initialize UI widgets state from current system settings
        self.sync_ui_with_system()
        
        # Start power monitoring thread (updates battery status)
        self.running = True
        self.power_thread = threading.Thread(target=self.monitor_power_loop, daemon=True)
        self.power_thread.start()

        # Check for updates on startup (startup=True asks user if update is found, silent on no update)
        self.check_for_updates_background(silent=True, startup=True)

    def init_win32_hooks(self):
        # Retrieve HWND of the Tkinter root window safely using wm_frame()
        try:
            self.hwnd = int(self.wm_frame(), 16)
        except Exception as e:
            print(f"Failed to get HWND via wm_frame: {e}")
            self.hwnd = ctypes.windll.user32.GetForegroundWindow()
            
        # Apply standard Windows 11 rounded corners via DWM API
        try:
            # DWMWA_WINDOW_CORNER_PREFERENCE = 33
            # DWMWCP_ROUND = 2 (round corners)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                self.hwnd, 
                33, 
                ctypes.byref(ctypes.c_int(2)), 
                4
            )
        except Exception as e:
            print(f"Failed to set rounded corners: {e}")

        # Apply native soft drop shadow on the frameless window
        try:
            margins = MARGINS(0, 0, 1, 0)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(self.hwnd, ctypes.byref(margins))
        except Exception as e:
            print(f"Failed to set shadow: {e}")

        # Subclass WndProc for this HWND to block shutdowns
        global _old_wndproc_ptr, _wndproc_callback_ref
        _wndproc_callback_ref = WNDPROC(my_wnd_proc)
        _old_wndproc_ptr = GetWindowLongPtr(self.hwnd, GWL_WNDPROC)
        SetWindowLongPtr(self.hwnd, GWL_WNDPROC, ctypes.cast(_wndproc_callback_ref, ctypes.c_void_p))

    def create_widgets(self):
        # 1. Top Banner (Gaia Pillars cropped slice)
        banner_frame = ctk.CTkFrame(self, height=110, corner_radius=0, fg_color=self.color_card)
        banner_frame.pack(fill="x", side="top")
        banner_frame.pack_propagate(False)
        
        # Load, crop, and draw text directly onto Banner Image (prevents gray boxes/borders in Tkinter)
        banner_label = None
        if os.path.exists(BANNER_PATH):
            try:
                pil_img = Image.open(BANNER_PATH)
                w, h = pil_img.size
                # Slice horizontal strip from center-ish
                crop_y1 = int(h * 0.15)
                crop_y2 = int(crop_y1 + (w * 110 / 380))
                if crop_y2 > h:
                    crop_y2 = h
                    crop_y1 = int(h - (w * 110 / 380))
                cropped_pil = pil_img.crop((0, crop_y1, w, crop_y2))
                
                # Resize to exactly 380x110
                cropped_pil = cropped_pil.resize((380, 110), Image.Resampling.LANCZOS)
                
                # Create a solid background matching the card color
                solid_bg = Image.new("RGB", (380, 110), color=self.color_card)
                
                # Create linear mask to fade the right 80px to self.color_card
                # (button sits around x=344 to x=370, fade is complete at x=340)
                mask = Image.new("L", (380, 110), color=255)
                mask_draw = ImageDraw.Draw(mask)
                for x in range(280, 340):
                    alpha = int(255 * (340 - x) / 60)
                    mask_draw.line([(x, 0), (x, 110)], fill=alpha)
                mask_draw.rectangle([(340, 0), (380, 110)], fill=0)
                
                # Composite cropped_pil onto the solid card background
                cropped_pil = Image.composite(cropped_pil, solid_bg, mask)
                
                # Draw text and credits directly on the image
                draw = ImageDraw.Draw(cropped_pil)
                
                # Load Segoe UI fonts
                try:
                    font_title = ImageFont.truetype("segoeuib.ttf", 16)
                    font_credit = ImageFont.truetype("segoeui.ttf", 9)
                except Exception:
                    font_title = ImageFont.load_default()
                    font_credit = ImageFont.load_default()
                
                # Render title with black drop-shadow for high legibility
                draw.text((13, 13), "AstroMode Controller", fill="#000000", font=font_title)
                draw.text((12, 12), "AstroMode Controller", fill="#ffffff", font=font_title)
                
                # Render credit text
                draw.text((12, 88), "Image: ESA/Gaia/DPAC", fill="#b8c0cc", font=font_credit)
                
                # Create CTkImage
                banner_img = ctk.CTkImage(
                    light_image=cropped_pil,
                    dark_image=cropped_pil,
                    size=(380, 110)
                )
                banner_label = ctk.CTkLabel(banner_frame, image=banner_img, text="")
                banner_label.place(x=0, y=0, relwidth=1, relheight=1)
            except Exception as e:
                print(f"Error loading/drawing banner image: {e}")
                
        if banner_label is None:
            fallback_label = ctk.CTkLabel(
                banner_frame, 
                text="★ ASTROMODE ★", 
                font=ctk.CTkFont(size=20, weight="bold"),
                text_color=self.color_text_main
            )
            fallback_label.pack(pady=40)

        # Dragging bindings (allows dragging the entire window from the banner area)
        banner_frame.bind("<Button-1>", self.start_drag)
        banner_frame.bind("<B1-Motion>", self.do_drag)
        banner_frame.bind("<ButtonRelease-1>", self.end_drag)
        if banner_label:
            banner_label.bind("<Button-1>", self.start_drag)
            banner_label.bind("<B1-Motion>", self.do_drag)
            banner_label.bind("<ButtonRelease-1>", self.end_drag)

        # Close button (rendered inside a rounded box in the top-right corner of the banner)
        close_btn = ctk.CTkButton(
            banner_frame, 
            text="✕", 
            width=26, 
            height=26, 
            fg_color="#1a2233",
            hover_color=self.color_accent_hover,
            text_color="#ffffff",
            font=ctk.CTkFont(size=11, weight="bold"),
            corner_radius=13,
            bg_color=self.color_card,
            command=self.hide_window
        )
        close_btn.place(relx=1.0, rely=0.0, anchor="ne", x=-10, y=10)

        # 2. Scrollable Container for Settings Cards
        self.scroll_frame = ctk.CTkScrollableFrame(
            self, 
            width=360, 
            height=370, 
            fg_color="transparent",
            scrollbar_button_color=self.color_accent,
            scrollbar_button_hover_color=self.color_accent_hover
        )
        self.scroll_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Card 1: Red Night Filter
        self.card_red_filter = self.create_card("Red Night Filter", "preserves night vision using hardware gamma ramp")
        self.chk_red_filter = ctk.CTkCheckBox(
            self.card_red_filter, text="Enable Red Screen", font=ctk.CTkFont(weight="bold"),
            text_color=self.color_text_main, border_color=self.color_accent,
            fg_color=self.color_accent, hover_color=self.color_accent_hover, command=self.on_setting_changed
        )
        self.chk_red_filter.pack(anchor="w", padx=10, pady=(5, 5))
        
        # Intensity Slider
        slider_frame = ctk.CTkFrame(self.card_red_filter, fg_color="transparent")
        slider_frame.pack(fill="x", padx=10, pady=(0, 5))
        lbl_intensity = ctk.CTkLabel(slider_frame, text="Intensity:", font=ctk.CTkFont(size=11), text_color=self.color_text_muted)
        lbl_intensity.pack(side="left", padx=(5, 5))
        self.slider_intensity = ctk.CTkSlider(
            slider_frame, from_=0.1, to=1.0, number_of_steps=9,
            button_color=self.color_accent, button_hover_color=self.color_accent_hover,
            progress_color=self.color_accent, fg_color=self.color_bg, command=self.update_red_filter_intensity
        )
        self.slider_intensity.set(1.0)
        self.slider_intensity.pack(side="right", fill="x", expand=True, padx=(5, 5))
        
        # Card 2: Dim Screen to Minimum
        self.card_dim = self.create_card("Display Brightness", "dim screen backlight to reduce glare")
        self.chk_dim = ctk.CTkCheckBox(
            self.card_dim, text="Force Dim Display", font=ctk.CTkFont(weight="bold"),
            text_color=self.color_text_main, border_color=self.color_accent,
            fg_color=self.color_accent, hover_color=self.color_accent_hover, command=self.on_setting_changed
        )
        self.chk_dim.pack(anchor="w", padx=10, pady=(5, 5))
        
        dim_frame = ctk.CTkFrame(self.card_dim, fg_color="transparent")
        dim_frame.pack(fill="x", padx=10, pady=(0, 5))
        lbl_dim = ctk.CTkLabel(dim_frame, text="Target Brightness:", font=ctk.CTkFont(size=11), text_color=self.color_text_muted)
        lbl_dim.pack(side="left", padx=(5, 5))
        self.slider_dim = ctk.CTkSlider(
            dim_frame, from_=0, to=20, number_of_steps=20,
            button_color=self.color_accent, button_hover_color=self.color_accent_hover,
            progress_color=self.color_accent, fg_color=self.color_bg, command=self.update_brightness_live
        )
        self.slider_dim.set(0)
        self.slider_dim.pack(side="right", fill="x", expand=True, padx=(5, 5))

        # Card 3: CPU Power Limiter
        self.card_cpu = self.create_card("CPU Power Limiter", "cap maximum processor speed to save battery and heat")
        self.chk_cpu = ctk.CTkCheckBox(
            self.card_cpu, text="Cap Maximum CPU State", font=ctk.CTkFont(weight="bold"),
            text_color=self.color_text_main, border_color=self.color_accent,
            fg_color=self.color_accent, hover_color=self.color_accent_hover, command=self.on_setting_changed
        )
        self.chk_cpu.pack(anchor="w", padx=10, pady=(5, 5))
        
        cpu_frame = ctk.CTkFrame(self.card_cpu, fg_color="transparent")
        cpu_frame.pack(fill="x", padx=10, pady=(0, 5))
        lbl_cpu = ctk.CTkLabel(cpu_frame, text="Max CPU Limit:", font=ctk.CTkFont(size=11), text_color=self.color_text_muted)
        lbl_cpu.pack(side="left", padx=(5, 5))
        
        self.lbl_cpu_val = ctk.CTkLabel(cpu_frame, text="50%", font=ctk.CTkFont(size=11, weight="bold"), text_color=self.color_accent)
        self.lbl_cpu_val.pack(side="right", padx=(5, 5))
        
        self.slider_cpu = ctk.CTkSlider(
            cpu_frame, from_=30, to=100, number_of_steps=14,
            button_color=self.color_accent, button_hover_color=self.color_accent_hover,
            progress_color=self.color_accent, fg_color=self.color_bg, command=self.on_cpu_slider_move
        )
        self.slider_cpu.set(50)
        self.slider_cpu.pack(side="right", fill="x", expand=True, padx=(5, 5))

        # Card 4: Prevent System Sleep
        self.card_sleep = self.create_card("Prevent Standby / Sleep", "blocks system sleep on battery and plugged in")
        self.chk_sleep = ctk.CTkCheckBox(
            self.card_sleep, text="Never Sleep / Block Standby", font=ctk.CTkFont(weight="bold"),
            text_color=self.color_text_main, border_color=self.color_accent,
            fg_color=self.color_accent, hover_color=self.color_accent_hover, command=self.on_setting_changed
        )
        self.chk_sleep.pack(anchor="w", padx=10, pady=(5, 8))

        # Card 5: Prevent Shutdown / Restarts
        self.card_shutdown = self.create_card("Prevent Shutdown / Restarts", "blocks Windows Update restarts or accidental power button presses")
        self.chk_shutdown = ctk.CTkCheckBox(
            self.card_shutdown, text="Block Shutdown & Restarts", font=ctk.CTkFont(weight="bold"),
            text_color=self.color_text_main, border_color=self.color_accent,
            fg_color=self.color_accent, hover_color=self.color_accent_hover, command=self.on_setting_changed
        )
        self.chk_shutdown.pack(anchor="w", padx=10, pady=(5, 8))

        # Card 6: Display Timeout Dropdown
        self.card_timeout = self.create_card("Display Timeout", "sets screen off timeout separately from system standby")
        self.chk_timeout = ctk.CTkCheckBox(
            self.card_timeout, text="Override Display Turn-Off", font=ctk.CTkFont(weight="bold"),
            text_color=self.color_text_main, border_color=self.color_accent,
            fg_color=self.color_accent, hover_color=self.color_accent_hover, command=self.on_setting_changed
        )
        self.chk_timeout.pack(anchor="w", padx=10, pady=(5, 5))
        
        timeout_frame = ctk.CTkFrame(self.card_timeout, fg_color="transparent")
        timeout_frame.pack(fill="x", padx=10, pady=(0, 5))
        lbl_time = ctk.CTkLabel(timeout_frame, text="Turn off screen after:", font=ctk.CTkFont(size=11), text_color=self.color_text_muted)
        lbl_time.pack(side="left", padx=(5, 5))
        self.opt_timeout = ctk.CTkOptionMenu(
            timeout_frame, 
            values=["1 Min", "2 Min", "5 Min", "10 Min", "15 Min", "30 Min", "1 Hour", "Never"],
            fg_color=self.color_bg, button_color=self.color_card, button_hover_color=self.color_border,
            dropdown_fg_color=self.color_card, dropdown_hover_color=self.color_accent,
            dropdown_text_color=self.color_text_main, text_color=self.color_text_main,
            command=self.on_timeout_combo_changed
        )
        self.opt_timeout.set("Never")
        self.opt_timeout.pack(side="right", padx=(5, 5))

        # Card 7: Keep USB Ports Powered
        self.card_usb = self.create_card("Keep USB Ports Powered", "prevents camera/mount disconnection by disabling USB selective suspend")
        self.chk_usb = ctk.CTkCheckBox(
            self.card_usb, text="Force Continuous USB Power", font=ctk.CTkFont(weight="bold"),
            text_color=self.color_text_main, border_color=self.color_accent,
            fg_color=self.color_accent, hover_color=self.color_accent_hover, command=self.on_setting_changed
        )
        self.chk_usb.pack(anchor="w", padx=10, pady=(5, 8))

        # Card 8: Lid Close Action
        self.card_lid = self.create_card("Lid Close Action", "do nothing when the laptop lid is closed (prevents sleep/shutdown)")
        self.chk_lid = ctk.CTkCheckBox(
            self.card_lid, text="Force Lid Close to 'Do Nothing'", font=ctk.CTkFont(weight="bold"),
            text_color=self.color_text_main, border_color=self.color_accent,
            fg_color=self.color_accent, hover_color=self.color_accent_hover, command=self.on_setting_changed
        )
        self.chk_lid.pack(anchor="w", padx=10, pady=(5, 8))

        # 3. Bottom Panel (Master Astro Switch, Power Status, and Autolaunch Option)
        bottom_frame = ctk.CTkFrame(self, height=110, corner_radius=0, fg_color=self.color_card, border_width=1, border_color=self.color_border)
        bottom_frame.pack(fill="x", side="bottom")
        bottom_frame.pack_propagate(False)
        
        # Power & Battery Stats
        self.lbl_power_status = ctk.CTkLabel(
            bottom_frame, 
            text="Power: Plugged In | Battery: 100%", 
            font=ctk.CTkFont(size=11), 
            text_color=self.color_text_muted
        )
        self.lbl_power_status.place(x=16, y=14)
        
        self.lbl_active_status = ctk.CTkLabel(
            bottom_frame, 
            text="Astro Mode is Inactive", 
            font=ctk.CTkFont(size=11, weight="bold"), 
            text_color=self.color_text_muted
        )
        self.lbl_active_status.place(x=16, y=40)
        
        # Auto-launch Startup Checkbox
        self.chk_autolaunch = ctk.CTkCheckBox(
            bottom_frame, text="Launch on Startup", font=ctk.CTkFont(size=10),
            text_color=self.color_text_muted, border_color=self.color_border,
            fg_color=self.color_accent, hover_color=self.color_accent_hover,
            height=18, width=18, command=self.on_autolaunch_toggled
        )
        self.chk_autolaunch.place(x=16, y=70)
        if config.data["autolaunch"]:
            self.chk_autolaunch.select()
        else:
            self.chk_autolaunch.deselect()

        # Update check button (badge in the footer)
        self.btn_update = ctk.CTkButton(
            bottom_frame,
            text=f"v{VERSION}",
            width=110,
            height=18,
            fg_color="#1a2233",
            hover_color=self.color_border,
            text_color=self.color_text_muted,
            font=ctk.CTkFont(size=9, weight="bold"),
            corner_radius=9,
            command=self.check_for_updates_manual
        )
        self.btn_update.place(x=235, y=70)
        
        # Master Astro Toggle
        self.switch_master = ctk.CTkSwitch(
            bottom_frame, 
            text="ASTRO MODE", 
            font=ctk.CTkFont(weight="bold", size=12),
            text_color=self.color_text_main,
            progress_color=self.color_accent, 
            fg_color=self.color_bg, 
            button_color=self.color_border, 
            button_hover_color=self.color_accent_hover,
            command=self.toggle_astro_mode
        )
        self.switch_master.place(x=235, y=38)

    def create_card(self, title, description):
        card = ctk.CTkFrame(
            self.scroll_frame, 
            fg_color=self.color_card, 
            border_width=1, 
            border_color=self.color_border, 
            corner_radius=12
        )
        card.pack(fill="x", pady=6, padx=2)
        
        lbl_title = ctk.CTkLabel(
            card, text=title, font=ctk.CTkFont(size=12, weight="bold"), text_color=self.color_text_main
        )
        lbl_title.pack(anchor="w", padx=10, pady=(8, 2))
        
        lbl_desc = ctk.CTkLabel(
            card, text=description, font=ctk.CTkFont(size=10), text_color=self.color_text_muted,
            wraplength=310, justify="left"
        )
        lbl_desc.pack(anchor="w", padx=10, pady=(0, 5))
        return card

    # ==============================================================================
    # SETTINGS LOGIC
    # ==============================================================================
    def sync_ui_with_system(self):
        # Initialize checkboxes from config
        if config.data["red_filter_enabled"]: self.chk_red_filter.select()
        else: self.chk_red_filter.deselect()
        self.slider_intensity.set(config.data["red_filter_intensity"])
        
        if config.data["dim_display_enabled"]: self.chk_dim.select()
        else: self.chk_dim.deselect()
        self.slider_dim.set(config.data["dim_display_target"])
        
        if config.data["cpu_limit_enabled"]: self.chk_cpu.select()
        else: self.chk_cpu.deselect()
        self.slider_cpu.set(config.data["cpu_limit_max"])
        self.lbl_cpu_val.configure(text=f"{config.data['cpu_limit_max']}%")
        
        if config.data["never_sleep_enabled"]: self.chk_sleep.select()
        else: self.chk_sleep.deselect()
        
        if config.data["prevent_shutdown_enabled"]: self.chk_shutdown.select()
        else: self.chk_shutdown.deselect()
        
        if config.data["display_timeout_enabled"]: self.chk_timeout.select()
        else: self.chk_timeout.deselect()
        self.opt_timeout.set(config.data["display_timeout_value"])
        
        if config.data["usb_power_enabled"]: self.chk_usb.select()
        else: self.chk_usb.deselect()

        if config.data.get("lid_close_enabled", True): self.chk_lid.select()
        else: self.chk_lid.deselect()
        if get_lid_close_settings() is None:
            self.chk_lid.configure(state="disabled", text="Lid Close Action (Not Supported)")

    def on_autolaunch_toggled(self):
        val = self.chk_autolaunch.get() == 1
        config.data["autolaunch"] = val
        config.save()
        set_autolaunch(val)

    def end_drag(self, event):
        config.data["last_x"] = self.winfo_x()
        config.data["last_y"] = self.winfo_y()
        config.save()

    def on_cpu_slider_move(self, val):
        self.lbl_cpu_val.configure(text=f"{int(val)}%")
        config.data["cpu_limit_max"] = int(val)
        config.save()
        # Live update if Astro Mode is active
        if self.astro_mode_active and self.chk_cpu.get():
            set_cpu_limits(int(val))

    def on_setting_changed(self):
        # Save check states to config
        config.data["red_filter_enabled"] = self.chk_red_filter.get() == 1
        config.data["dim_display_enabled"] = self.chk_dim.get() == 1
        config.data["cpu_limit_enabled"] = self.chk_cpu.get() == 1
        config.data["never_sleep_enabled"] = self.chk_sleep.get() == 1
        config.data["prevent_shutdown_enabled"] = self.chk_shutdown.get() == 1
        config.data["display_timeout_enabled"] = self.chk_timeout.get() == 1
        config.data["usb_power_enabled"] = self.chk_usb.get() == 1
        config.data["lid_close_enabled"] = self.chk_lid.get() == 1
        config.save()

        # If Astro Mode is currently active, we want to immediately apply
        # any newly checked/unchecked toggles!
        if self.astro_mode_active:
            self.apply_astro_mode_settings()

    def update_red_filter_intensity(self, val):
        config.data["red_filter_intensity"] = float(val)
        config.save()
        # Live update red filter ramp if it is currently active
        if self.astro_mode_active and self.chk_red_filter.get():
            self.apply_red_filter(val)

    def update_brightness_live(self, val):
        config.data["dim_display_target"] = int(val)
        config.save()
        # Live update brightness if it is active
        if self.astro_mode_active and self.chk_dim.get():
            set_system_brightness(int(val))

    def on_timeout_combo_changed(self, val):
        config.data["display_timeout_value"] = val
        config.save()
        # Live update display timeout if active
        if self.astro_mode_active and self.chk_timeout.get():
            sec = self.get_timeout_seconds(val)
            set_display_timeouts(sec, sec)

    def get_timeout_seconds(self, str_val):
        if "Min" in str_val:
            return int(str_val.split()[0]) * 60
        elif "Hour" in str_val:
            return 3600
        return 0 # 0 is Never

    # ==============================================================================
    # ASTRO MODE STATE MODIFICATION
    # ==============================================================================
    def toggle_astro_mode(self):
        if self.switch_master.get() == 1:
            self.enable_astro_mode()
        else:
            self.disable_astro_mode()

    def enable_astro_mode(self):
        self.astro_mode_active = True
        self.lbl_active_status.configure(text="ASTRO MODE ACTIVE", text_color=self.color_accent)
        self.apply_astro_mode_settings()

    def disable_astro_mode(self):
        self.astro_mode_active = False
        self.lbl_active_status.configure(text="Astro Mode is Inactive", text_color=self.color_text_muted)
        
        # Restore system settings using the state manager
        state_manager.restore_system_settings()
        
        # Remove win32 execution block
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        
        # Remove shutdown block
        global _block_shutdown_active
        _block_shutdown_active = False
        if self.hwnd:
            ctypes.windll.user32.ShutdownBlockReasonDestroy(self.hwnd)

    def apply_astro_mode_settings(self):
        """Applies/updates all system configurations based on selected checkboxes."""
        if not self.astro_mode_active:
            return

        print("Applying selected Astro Mode configurations...")

        # Keep a list of currently selected features to check against
        active_features = set()

        # 1. Red Screen Filter
        if self.chk_red_filter.get():
            active_features.add("red_filter")
            self.apply_red_filter(self.slider_intensity.get())
        else:
            # If red filter was turned off individually, restore gamma ramp
            restore_gamma_linear()

        # 2. Dim Screen
        if self.chk_dim.get():
            active_features.add("dim_display")
            # Save original brightness before changing
            orig_b = get_system_brightness()
            # If we already saved it in this session, don't overwrite
            if state_manager.get_original_value("brightness") is None:
                state_manager.save_original_value("brightness", orig_b)
            set_system_brightness(int(self.slider_dim.get()))
        else:
            # Restore brightness if unchecked
            orig_b = state_manager.get_original_value("brightness")
            if orig_b is not None:
                set_system_brightness(orig_b)
                state_manager.backup_data.pop("brightness", None)
                state_manager.save_backup()

        # 3. CPU Limiter
        if self.chk_cpu.get():
            active_features.add("cpu_limit")
            ac_cpu, dc_cpu = get_cpu_limits()
            if state_manager.get_original_value("cpu_limits") is None:
                state_manager.save_original_value("cpu_limits", (ac_cpu, dc_cpu))
                
            ac_min_cpu, dc_min_cpu = get_min_cpu_limits()
            if state_manager.get_original_value("min_cpu_limits") is None:
                state_manager.save_original_value("min_cpu_limits", (ac_min_cpu, dc_min_cpu))
                
            set_cpu_limits(int(self.slider_cpu.get()))
            set_min_cpu_limits(0) # Force min CPU state to 0% to allow max down-throttling
        else:
            orig_cpu = state_manager.get_original_value("cpu_limits")
            if orig_cpu is not None:
                set_cpu_limits(orig_cpu[0])
                state_manager.backup_data.pop("cpu_limits", None)
                
            orig_min_cpu = state_manager.get_original_value("min_cpu_limits")
            if orig_min_cpu is not None:
                set_min_cpu_limits(orig_min_cpu[0])
                state_manager.backup_data.pop("min_cpu_limits", None)
                
            state_manager.save_backup()

        # 4. Never Sleep / Prevent Standby
        if self.chk_sleep.get():
            active_features.add("never_sleep")
            ac_standby, dc_standby = get_standby_timeouts()
            if state_manager.get_original_value("standby_timeouts") is None:
                state_manager.save_original_value("standby_timeouts", (ac_standby, dc_standby))
            set_standby_timeouts(0, 0) # 0 = Never
            
            # Prevent standby at OS level
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)
        else:
            orig_standby = state_manager.get_original_value("standby_timeouts")
            if orig_standby is not None:
                set_standby_timeouts(orig_standby[0], orig_standby[1])
                state_manager.backup_data.pop("standby_timeouts", None)
                state_manager.save_backup()
                
            # Release OS standby block if no other block is active
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)

        # 5. Prevent Shutdown & Restarts
        if self.chk_shutdown.get():
            active_features.add("prevent_shutdown")
            global _block_shutdown_active
            _block_shutdown_active = True
            if self.hwnd:
                ctypes.windll.user32.ShutdownBlockReasonCreate(
                    self.hwnd, 
                    ctypes.c_wchar_p("Astrophotography run in progress! Please close AstroMode first.")
                )
            
            # Disable automatic hibernate
            ac_hib, dc_hib = get_hibernate_timeouts()
            if state_manager.get_original_value("hibernate_timeouts") is None:
                state_manager.save_original_value("hibernate_timeouts", (ac_hib, dc_hib))
            set_hibernate_timeouts(0, 0)
        else:
            _block_shutdown_active = False
            if self.hwnd:
                ctypes.windll.user32.ShutdownBlockReasonDestroy(self.hwnd)
                
            orig_hib = state_manager.get_original_value("hibernate_timeouts")
            if orig_hib is not None:
                set_hibernate_timeouts(orig_hib[0], orig_hib[1])
                state_manager.backup_data.pop("hibernate_timeouts", None)
                state_manager.save_backup()

        # 6. Display Timeout Overrides
        if self.chk_timeout.get():
            active_features.add("display_timeout")
            ac_disp, dc_disp = get_display_timeouts()
            if state_manager.get_original_value("display_timeouts") is None:
                state_manager.save_original_value("display_timeouts", (ac_disp, dc_disp))
            sec = self.get_timeout_seconds(self.opt_timeout.get())
            set_display_timeouts(sec, sec)
        else:
            orig_disp = state_manager.get_original_value("display_timeouts")
            if orig_disp is not None:
                set_display_timeouts(orig_disp[0], orig_disp[1])
                state_manager.backup_data.pop("display_timeouts", None)
                state_manager.save_backup()

        # 7. Keep USB Ports Powered
        if self.chk_usb.get():
            active_features.add("usb_power")
            ac_usb, dc_usb = get_usb_settings()
            if state_manager.get_original_value("usb_selective_suspend") is None:
                state_manager.save_original_value("usb_selective_suspend", (ac_usb, dc_usb))
            set_usb_settings(0, 0) # 0 = Disabled
        else:
            orig_usb = state_manager.get_original_value("usb_selective_suspend")
            if orig_usb is not None:
                set_usb_settings(orig_usb[0], orig_usb[1])
                state_manager.backup_data.pop("usb_selective_suspend", None)
                state_manager.save_backup()

        # 8. Lid Close Action
        if self.chk_lid.get() and get_lid_close_settings() is not None:
            active_features.add("lid_close")
            ac_lid, dc_lid = get_lid_close_settings()
            if state_manager.get_original_value("lid_close_action") is None:
                state_manager.save_original_value("lid_close_action", (ac_lid, dc_lid))
            set_lid_close_settings(0, 0) # 0 = Do Nothing
        else:
            orig_lid = state_manager.get_original_value("lid_close_action")
            if orig_lid is not None:
                set_lid_close_settings(orig_lid[0], orig_lid[1])
                state_manager.backup_data.pop("lid_close_action", None)
                state_manager.save_backup()

        # Update backup state on disk
        state_manager.save_backup()

    def apply_red_filter(self, intensity):
        hdc = ctypes.windll.user32.GetDC(0)
        if hdc:
            red_ramp = RAMP()
            # Blend green and blue channels from normal (at intensity=0.0) to 0 (at intensity=1.0)
            factor = 1.0 - intensity
            for i in range(256):
                red_ramp.red[i] = int(i * 256)
                red_ramp.green[i] = int(i * 256 * factor)
                red_ramp.blue[i] = int(i * 256 * factor)
            
            ctypes.windll.gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(red_ramp))
            ctypes.windll.user32.ReleaseDC(0, hdc)

    # ==============================================================================
    # WINDOW CONTROLS & FOCUS HANDLING
    # ==============================================================================
    def start_drag(self, event):
        self.drag_start_x = event.x_root - self.winfo_x()
        self.drag_start_y = event.y_root - self.winfo_y()

    def do_drag(self, event):
        x = event.x_root - self.drag_start_x
        y = event.y_root - self.drag_start_y
        self.geometry(f"+{x}+{y}")

    def show_window(self):
        # Default geometry sizes
        w_width = 380
        w_height = 620
        
        # Position window near bottom-right corner of work area or load remembered position
        if config.data["last_x"] is not None and config.data["last_y"] is not None:
            x = config.data["last_x"]
            y = config.data["last_y"]
        else:
            rect = wintypes.RECT()
            # SPI_GETWORKAREA = 48
            ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
            
            # Determine current screen DPI scale factor
            try:
                dpi_scale = self.winfo_fpixels('1i') / 96.0
            except Exception:
                dpi_scale = 1.0
                
            # Convert raw pixels from SystemParametersInfoW into scaled coordinates
            work_right = int(rect.right / dpi_scale)
            work_bottom = int(rect.bottom / dpi_scale)
            
            # Offset 16px from taskbar and screen edges for a beautiful hover effect
            x = work_right - w_width - 16
            y = work_bottom - w_height - 16
            
        self.geometry(f"{w_width}x{w_height}+{x}+{y}")
        self.deiconify()
        self.attributes("-topmost", True) # Make sure it starts topmost
        self.focus_force()

    def check_for_updates_background(self, silent=True, startup=False):
        def worker():
            try:
                # Add User-Agent header as required by GitHub API
                req = urllib.request.Request(
                    "https://api.github.com/repos/Starman42X/astro-mode/releases/latest",
                    headers={"User-Agent": "AstroMode-Updater"}
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.loads(response.read().decode())
                    
                tag = data.get("tag_name", "")
                if not tag:
                    def no_release_found():
                        self.btn_update.configure(text=f"v{VERSION}", state="normal")
                        if not silent and not startup:
                            self.show_custom_dialog(
                                title="AstroMode Update",
                                message="No releases found on GitHub yet. You are running the latest version!"
                            )
                    self.after(0, no_release_found)
                    return
                    
                latest_version = tag.lstrip('v')
                current_version = VERSION.lstrip('v')
                
                # Check if tag is newer using semantic versioning
                if is_newer_version(latest_version, current_version):
                    # Find exe asset
                    exe_url = None
                    for asset in data.get("assets", []):
                        if asset.get("name", "").endswith(".exe"):
                            exe_url = asset.get("browser_download_url")
                            break
                    
                    if exe_url:
                        def notify():
                            # Highlight button in orange to grab attention!
                            self.btn_update.configure(
                                text=f"Update to {tag}", 
                                fg_color=self.color_accent,
                                hover_color=self.color_accent_hover,
                                text_color="#ffffff",
                                state="normal",
                                command=lambda: self.start_update(exe_url, tag)
                            )
                            # If startup or manual check, prompt user
                            if startup or not silent:
                                self.show_custom_dialog(
                                    title="Update Available",
                                    message=f"A new version ({tag}) of AstroMode is available.\n\nWould you like to download and install it now?",
                                    confirm_text="Update Now",
                                    cancel_text="Later",
                                    on_confirm=lambda: self.start_update(exe_url, tag)
                                )
                        self.after(0, notify)
                        return
                    else:
                        def notify_no_exe():
                            self.btn_update.configure(text=f"New Release: {tag}", state="disabled")
                            if startup or not silent:
                                self.show_custom_dialog(
                                    title="Release Asset Missing",
                                    message=f"A new version ({tag}) is available on GitHub, but no executable (.exe) was found in the release assets.\n\nPlease verify that the executable is attached to the GitHub release.",
                                    confirm_text="OK"
                                )
                        self.after(0, notify_no_exe)
                        return
                
                def no_update():
                    self.btn_update.configure(text=f"v{VERSION}", state="normal")
                    if not silent and not startup:
                        self.show_custom_dialog(
                            title="AstroMode Update",
                            message="You are running the latest version of AstroMode!"
                        )
                self.after(0, no_update)
                
            except urllib.error.HTTPError as e:
                print(f"HTTP Error checking for updates: {e.code} - {e.reason}")
                if e.code == 404:
                    # Let's verify if the repository itself exists/is public
                    repo_exists = False
                    try:
                        req_repo = urllib.request.Request(
                            "https://api.github.com/repos/Starman42X/astro-mode",
                            headers={"User-Agent": "AstroMode-Updater"}
                        )
                        with urllib.request.urlopen(req_repo, timeout=5) as resp:
                            if resp.status == 200:
                                repo_exists = True
                    except Exception:
                        pass
                        
                    if repo_exists:
                        # Repository is public but simply has no releases yet
                        def no_release():
                            self.btn_update.configure(text=f"v{VERSION}", state="normal")
                            if not silent and not startup:
                                self.show_custom_dialog(
                                    title="AstroMode Update",
                                    message="No releases found on GitHub yet. You are running the latest version!"
                                )
                        self.after(0, no_release)
                    else:
                        # Repository is private or does not exist
                        def repo_private():
                            self.btn_update.configure(text="Check Failed", state="normal")
                            if not silent and not startup:
                                self.show_custom_dialog(
                                    title="Update Check Failed",
                                    message="The repository 'Starman42X/astro-mode' is private or not found (404).\n\nPlease ensure your GitHub repository is set to 'Public' so that the updater can check and download releases."
                                )
                            self.after(3000, lambda: self.btn_update.configure(text=f"v{VERSION}"))
                        self.after(0, repo_private)
                else:
                    def report_http_error():
                        self.btn_update.configure(text="Check Failed", state="normal")
                        if not silent and not startup:
                            self.show_custom_dialog(
                                title="Update Error",
                                message=f"HTTP Error {e.code}: {e.reason}"
                            )
                        self.after(3000, lambda: self.btn_update.configure(text=f"v{VERSION}"))
                    self.after(0, report_http_error)
            except Exception as e:
                print(f"Error checking for updates: {e}")
                def report_error():
                    self.btn_update.configure(text="Check Failed", state="normal")
                    if not silent and not startup:
                        self.show_custom_dialog(
                            title="Update Error",
                            message=f"Failed to check for updates:\n\n{e}"
                        )
                    self.after(3000, lambda: self.btn_update.configure(text=f"v{VERSION}"))
                self.after(0, report_error)
                    
        threading.Thread(target=worker, daemon=True).start()

    def check_for_updates_manual(self):
        self.btn_update.configure(text="Checking...", state="disabled")
        self.check_for_updates_background(silent=False)

    def start_update(self, exe_url, version_tag):
        self.btn_update.configure(state="disabled", text="Downloading...")
        
        def worker():
            try:
                # If running in python development mode (not frozen), simulate the update
                if not getattr(sys, 'frozen', False):
                    def dev_notify():
                        self.show_custom_dialog(
                            title="Update Simulated",
                            message=f"Update simulated in developer mode.\n\nNew Version: {version_tag}\nDownload URL: {exe_url}",
                            confirm_text="OK"
                        )
                        # Reset button back to default
                        self.btn_update.configure(
                            text=f"v{VERSION}",
                            state="normal",
                            fg_color="#1a2233",
                            hover_color=self.color_border,
                            text_color=self.color_text_muted,
                            command=self.check_for_updates_manual
                        )
                    self.after(0, dev_notify)
                    return
                
                exe_path = sys.executable
                exe_dir = os.path.dirname(exe_path)
                new_exe_path = os.path.join(exe_dir, "AstroMode_new.exe")
                
                # Download new executable file
                req = urllib.request.Request(exe_url, headers={"User-Agent": "AstroMode-Updater"})
                with urllib.request.urlopen(req) as response:
                    with open(new_exe_path, "wb") as f:
                        f.write(response.read())
                
                # Detached PowerShell execution script
                pid = os.getpid()
                exe_name = os.path.basename(exe_path)
                
                ps_script = f"""
                Remove-Item env:_MEIPASS -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 1
                $count = 0
                while ((Get-Process -Id {pid} -ErrorAction SilentlyContinue) -and ($count -lt 10)) {{
                    Start-Sleep -Milliseconds 200
                    $count++
                }}
                Remove-Item -Path "{exe_path}" -Force -ErrorAction SilentlyContinue
                Rename-Item -Path "{new_exe_path}" -NewName "{exe_name}" -Force
                Start-Process -FilePath "{exe_path}"
                """
                
                # Spawn PowerShell in background with hidden window and no console window
                cmd = ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script]
                env = os.environ.copy()
                env.pop("_MEIPASS", None)
                subprocess.Popen(cmd, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
                
                # Shutdown current app immediately to release file lock
                self.after(0, self.on_exit)
                
            except Exception as e:
                def report_error(err_msg=str(e)):
                    self.show_custom_dialog(
                        title="Update Error",
                        message=f"Failed to install update:\n\n{err_msg}",
                        confirm_text="OK"
                    )
                    self.btn_update.configure(
                        text=f"Update to {version_tag}",
                        fg_color=self.color_accent,
                        hover_color=self.color_accent_hover,
                        text_color="#ffffff",
                        state="normal"
                    )
                self.after(0, report_error)
                
        threading.Thread(target=worker, daemon=True).start()

    def hide_window(self):
        self.withdraw()

    def show_custom_dialog(self, title, message, confirm_text="OK", cancel_text=None, on_confirm=None, on_cancel=None):
        # If there is already a dialog open, destroy it first
        if hasattr(self, "custom_dialog_frame") and self.custom_dialog_frame:
            try:
                self.custom_dialog_frame.destroy()
            except Exception:
                pass

        # Create dimming background cover to make the overlay stand out
        # We use a very dark solid color matching the cosmic background
        dialog_cover = ctk.CTkFrame(self, fg_color="#04060a")
        dialog_cover.place(x=0, y=0, relwidth=1, relheight=1)
        
        # Bind mouse clicks to prevent interacting with background widgets
        dialog_cover.bind("<Button-1>", lambda e: "break")

        # Dialog Box Container
        dialog_box = ctk.CTkFrame(
            dialog_cover, 
            fg_color=self.color_card, 
            border_width=2, 
            border_color=self.color_accent,
            corner_radius=16,
            width=320,
            height=200
        )
        dialog_box.place(relx=0.5, rely=0.5, anchor="center")
        dialog_box.pack_propagate(False)

        # Dialog Title
        lbl_title = ctk.CTkLabel(
            dialog_box, 
            text=title, 
            font=ctk.CTkFont(size=14, weight="bold"), 
            text_color=self.color_text_main
        )
        lbl_title.pack(pady=(15, 10), padx=15)

        # Dialog Message
        lbl_msg = ctk.CTkLabel(
            dialog_box, 
            text=message, 
            font=ctk.CTkFont(size=11), 
            text_color=self.color_text_muted,
            wraplength=280,
            justify="center"
        )
        lbl_msg.pack(pady=(0, 15), padx=15, fill="both", expand=True)

        # Buttons Frame
        btn_frame = ctk.CTkFrame(dialog_box, fg_color="transparent")
        btn_frame.pack(side="bottom", fill="x", pady=(0, 15), padx=15)

        def close_dialog():
            dialog_cover.destroy()
            self.custom_dialog_frame = None

        def handle_confirm():
            close_dialog()
            if on_confirm:
                on_confirm()

        def handle_cancel():
            close_dialog()
            if on_cancel:
                on_cancel()

        # Cancel Button
        if cancel_text:
            btn_cancel = ctk.CTkButton(
                btn_frame,
                text=cancel_text,
                fg_color="#1a2233",
                hover_color=self.color_border,
                text_color=self.color_text_muted,
                font=ctk.CTkFont(size=11, weight="bold"),
                height=32,
                corner_radius=8,
                command=handle_cancel
            )
            btn_cancel.pack(side="left", fill="x", expand=True, padx=(0, 5))

        # Confirm Button
        btn_confirm = ctk.CTkButton(
            btn_frame,
            text=confirm_text,
            fg_color=self.color_accent,
            hover_color=self.color_accent_hover,
            text_color="#ffffff",
            font=ctk.CTkFont(size=11, weight="bold"),
            height=32,
            corner_radius=8,
            command=handle_confirm
        )
        if cancel_text:
            btn_confirm.pack(side="right", fill="x", expand=True, padx=(5, 0))
        else:
            btn_confirm.pack(fill="x", expand=True)

        self.custom_dialog_frame = dialog_cover

    def check_recovery(self):
        self.show_custom_dialog(
            title="AstroMode Recovery",
            message="It looks like AstroMode closed unexpectedly last time.\n\nWould you like to restore your laptop settings to their original state?",
            confirm_text="Restore Settings",
            cancel_text="Keep Settings",
            on_confirm=state_manager.restore_system_settings,
            on_cancel=state_manager.clear_backup
        )

    def on_focus_out(self, event):
        # We only want to remove topmost if the active window is not our window.
        # This prevents losing topmost when clicking empty spaces or dragging the window.
        self.after(50, self._check_focus_lost)

    def _check_focus_lost(self):
        try:
            foreground_hwnd = ctypes.windll.user32.GetForegroundWindow()
            if self.hwnd and foreground_hwnd != self.hwnd:
                # Focus has actually left our application
                self.attributes("-topmost", False)
        except Exception:
            self.attributes("-topmost", False)

    def on_focus_in(self, event):
        self.attributes("-topmost", True)

    # ==============================================================================
    # POWER STATUS MONITOR THREAD
    # ==============================================================================
    def monitor_power_loop(self):
        while self.running:
            ac_state, battery_percent = get_system_power_status()
            
            # Safe UI update via after()
            def update_label(ac=ac_state, bat=battery_percent):
                self.lbl_power_status.configure(text=f"Power: {ac} | Battery: {bat}")
                
            self.after(0, update_label)
            time.sleep(5)

    # ==============================================================================
    # SYSTEM EXIT AND DESTROY
    # ==============================================================================
    def on_exit(self):
        self.running = False
        
        # Turn off astro mode if it was active (this restores all laptop settings!)
        if self.astro_mode_active:
            self.disable_astro_mode()
            
        # Clean up Win32 WndProc subclass hook
        if self.hwnd:
            unsubclass_window(self.hwnd)
            
        # Destroy tray icon
        if self.icon:
            self.icon.stop()
            
        self.destroy()
        sys.exit(0)

def unsubclass_window(hwnd):
    global _old_wndproc_ptr
    if _old_wndproc_ptr is not None:
        SetWindowLongPtr(hwnd, GWL_WNDPROC, _old_wndproc_ptr)
        _old_wndproc_ptr = None


# ==============================================================================
# SYSTEM TRAY INTEGRATION (PYSTRAY)
# ==============================================================================
def create_tray_image():
    # Load custom icon from workspace
    if os.path.exists(ICON_PATH):
        try:
            return Image.open(ICON_PATH)
        except Exception as e:
            print(f"Error loading icon image: {e}")
            
    # Fallback to simple generated image
    image = Image.new('RGB', (64, 64), color='#0b0f19')
    dc = ImageDraw.Draw(image)
    # Draw simple red telescope or target
    dc.ellipse([16, 16, 48, 48], outline='#e05a36', width=4)
    dc.line([32, 16, 32, 48], fill='#e05a36', width=2)
    dc.line([16, 32, 48, 32], fill='#e05a36', width=2)
    return image

def on_tray_clicked(icon, item, app):
    if str(item) == "Dashboard":
        app.after(0, app.show_window)
    elif str(item) == "Toggle Astro Mode":
        def quick_toggle():
            if app.astro_mode_active:
                app.switch_master.deselect()
                app.disable_astro_mode()
            else:
                app.switch_master.select()
                app.enable_astro_mode()
        app.after(0, quick_toggle)
    elif str(item) == "Exit":
        app.after(0, app.on_exit)

def run_tray_icon(app):
    menu = pystray.Menu(
        pystray.MenuItem("Dashboard", lambda icon, item: on_tray_clicked(icon, item, app), default=True),
        pystray.MenuItem("Toggle Astro Mode", lambda icon, item: on_tray_clicked(icon, item, app)),
        pystray.MenuItem("Exit", lambda icon, item: on_tray_clicked(icon, item, app))
    )
    
    app.icon = pystray.Icon(
        "astromode", 
        create_tray_image(), 
        "AstroMode Controller", 
        menu=menu
    )
    app.icon.run()


# ==============================================================================
# ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    app = AstroModeApp()
    
    # Hide window by default, let it live in the system tray
    # (The user will open it by clicking the tray icon)
    # To let them know it started, we will show it once on launch!
    app.show_window()
    
    # Run the system tray icon in a separate thread
    tray_thread = threading.Thread(target=run_tray_icon, args=(app,), daemon=True)
    tray_thread.start()
    
    # Main Tkinter loop
    app.mainloop()
