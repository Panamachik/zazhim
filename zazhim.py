"""Зажим — зажимает выбранную клавишу или кнопку мыши.
Горячая клавиша F8 включает и отпускает зажатие из любого окна.
"""
import string
import tkinter as tk
from tkinter import ttk

from pynput import keyboard, mouse

kb = keyboard.Controller()
ms = mouse.Controller()

KEYS = {
    "Пробел": keyboard.Key.space,
    "Shift": keyboard.Key.shift,
    "Ctrl": keyboard.Key.ctrl,
    "Alt": keyboard.Key.alt,
    "Enter": keyboard.Key.enter,
    "Tab": keyboard.Key.tab,
    "Стрелка вверх": keyboard.Key.up,
    "Стрелка вниз": keyboard.Key.down,
    "Стрелка влево": keyboard.Key.left,
    "Стрелка вправо": keyboard.Key.right,
}
for n in range(1, 13):
    if n != 8:  # F8 занята под горячую клавишу
        KEYS[f"F{n}"] = getattr(keyboard.Key, f"f{n}")
for ch in string.ascii_lowercase + string.digits:
    KEYS[ch.upper()] = ch

MOUSE = {
    "Мышь: левая": mouse.Button.left,
    "Мышь: правая": mouse.Button.right,
    "Мышь: средняя": mouse.Button.middle,
}

ALL = list(MOUSE) + list(KEYS)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.holding = False
        self.target = None  # то, что сейчас зажато

        root.title("Зажим")
        root.geometry("340x250")
        root.resizable(False, False)
        root.attributes("-topmost", True)

        frm = ttk.Frame(root, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Что зажимать").pack(anchor="w")
        self.choice = ttk.Combobox(frm, values=ALL, state="readonly")
        self.choice.set("W")
        self.choice.pack(fill="x", pady=(2, 10))

        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(0, 12))
        ttk.Label(row, text="Задержка перед зажатием, с").pack(side="left")
        self.delay = tk.IntVar(value=3)
        ttk.Spinbox(row, from_=0, to=30, width=4, textvariable=self.delay).pack(side="right")

        self.btn = ttk.Button(frm, text="Зажать (F8)", command=self.toggle)
        self.btn.pack(fill="x", ipady=8)

        self.status = ttk.Label(frm, text="Отпущено", foreground="#666")
        self.status.pack(pady=(12, 0))

        # Глобальная горячая клавиша F8
        self.hotkey = keyboard.GlobalHotKeys({"<f8>": lambda: root.after(0, self.toggle)})
        self.hotkey.start()

        root.protocol("WM_DELETE_WINDOW", self.close)

    def toggle(self):
        if self.holding:
            self.release()
        else:
            wait = max(0, self.delay.get())
            self.btn.config(state="disabled")
            if wait:
                self.countdown(wait)
            else:
                self.press()

    def countdown(self, left):
        if left == 0:
            self.press()
            return
        self.status.config(text=f"Зажму через {left} с — переключитесь в нужное окно")
        self.root.after(1000, lambda: self.countdown(left - 1))

    def press(self):
        name = self.choice.get()
        if name in MOUSE:
            self.target = MOUSE[name]
            ms.press(self.target)
        else:
            self.target = KEYS[name]
            kb.press(self.target)
        self.holding = True
        self.btn.config(text="Отпустить (F8)", state="normal")
        self.status.config(text=f"Зажато: {name}", foreground="#0a7d33")

    def release(self):
        if self.target is not None:
            if isinstance(self.target, mouse.Button):
                ms.release(self.target)
            else:
                kb.release(self.target)
        self.holding = False
        self.target = None
        self.btn.config(text="Зажать (F8)", state="normal")
        self.status.config(text="Отпущено", foreground="#666")

    def close(self):
        if self.holding:
            self.release()
        self.hotkey.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
