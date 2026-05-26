import tkinter as tk
import pystray
from PIL import Image, ImageDraw
import threading
import time

def create_image():
    # Generate a simple red circle image for the icon
    image = Image.new('RGB', (64, 64), color='black')
    dc = ImageDraw.Draw(image)
    dc.ellipse([16, 16, 48, 48], fill='red')
    return image

def on_clicked(icon, item):
    if str(item) == "Open":
        show_window()
    elif str(item) == "Exit":
        icon.stop()
        root.after(0, root.destroy)

def show_window():
    root.after(0, lambda: (root.deiconify(), root.focus_force()))

def hide_window():
    root.withdraw()

def main():
    global root
    root = tk.Tk()
    root.title("AstroMode Test")
    root.geometry("300x200")
    
    # Override close event to hide instead of destroy
    root.protocol('WM_DELETE_WINDOW', hide_window)
    
    label = tk.Label(root, text="Hello Astro Mode!")
    label.pack(pady=20)
    
    # Create tray icon
    icon = pystray.Icon("astromode", create_image(), "AstroMode", menu=pystray.Menu(
        pystray.MenuItem("Open", on_clicked),
        pystray.MenuItem("Exit", on_clicked)
    ))
    
    # Run pystray in a background thread
    tray_thread = threading.Thread(target=icon.run, daemon=True)
    tray_thread.start()
    
    root.mainloop()

if __name__ == "__main__":
    main()
