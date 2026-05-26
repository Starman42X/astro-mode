import ctypes
import time

class RAMP(ctypes.Structure):
    _fields_ = [("red", ctypes.c_ushort * 256),
                ("green", ctypes.c_ushort * 256),
                ("blue", ctypes.c_ushort * 256)]

def test_gamma():
    hdc = ctypes.windll.user32.GetDC(0)
    if not hdc:
        print("Failed to get DC")
        return

    original_ramp = RAMP()
    res_get = ctypes.windll.gdi32.GetDeviceGammaRamp(hdc, ctypes.byref(original_ramp))
    print(f"GetDeviceGammaRamp result: {res_get}")

    if res_get:
        # Create red ramp
        red_ramp = RAMP()
        for i in range(256):
            red_ramp.red[i] = int(i * 256)
            red_ramp.green[i] = 0
            red_ramp.blue[i] = 0

        # Try to set
        res_set = ctypes.windll.gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(red_ramp))
        print(f"SetDeviceGammaRamp result: {res_set}")
        
        if res_set:
            print("Successfully set to red. Waiting 2 seconds before restoring...")
            time.sleep(2)
            res_restore = ctypes.windll.gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(original_ramp))
            print(f"Restore result: {res_restore}")
        else:
            print("Failed to set gamma ramp (often happens on laptops with HDR or hybrid graphics)")
    else:
        print("Failed to get gamma ramp")
        
    ctypes.windll.user32.ReleaseDC(0, hdc)

if __name__ == "__main__":
    test_gamma()
