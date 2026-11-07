"""Зажим — две программы в одном окне.
Вкладка «Зажим»: держит до трёх клавиш или кнопок мыши (F8).
Вкладка «Макрос»: записывает движения мыши и нажатия, потом повторяет (F9 запись, F10 пуск/стоп).
"""
import ctypes
import string
import threading
import time
import tkinter as tk
from tkinter import ttk

from pynput import keyboard, mouse

# Windows: одинаковые координаты мыши при записи и воспроизведении на экранах с масштабом
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
# Windows: точный таймер (1 мс вместо ~15 мс), чтобы повтор шёл без сбивания по времени
try:
    ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:
    pass

kb = keyboard.Controller()
ms = mouse.Controller()

NONE = "—"
HOTKEYS = (keyboard.Key.f8, keyboard.Key.f9, keyboard.Key.f10)

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
    if n not in (8, 9, 10):  # F8, F9, F10 заняты горячими клавишами
        KEYS[f"F{n}"] = getattr(keyboard.Key, f"f{n}")
for ch in string.ascii_lowercase + string.digits:
    KEYS[ch.upper()] = ch

MOUSE = {
    "Мышь: левая": mouse.Button.left,
    "Мышь: правая": mouse.Button.right,
    "Мышь: средняя": mouse.Button.middle,
}

ALL = [NONE] + list(MOUSE) + list(KEYS)


# ---------- воспроизведение макроса (без интерфейса, чтобы не зависеть от окна) ----------

MODE_NORMAL = "Обычный"
MODE_GAME = "Игровой"
MIN_HOLD = 0.05  # клик держим не меньше 50 мс: так игра точно его заметит
SETTLE = 0.02    # пауза после наведения курсора перед нажатием


def move_cursor(x, y, mode):
    if mode == MODE_GAME:
        # «Игровой»: настоящее событие ввода (SendInput), а не просто перестановка курсора
        try:
            u = ctypes.windll.user32
            left, top = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
            w, h = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
            nx = int(round((x - left) * 65535 / max(w - 1, 1)))
            ny = int(round((y - top) * 65535 / max(h - 1, 1)))
            nx = min(max(nx, 0), 65535)
            ny = min(max(ny, 0), 65535)
            u.mouse_event(0x0001 | 0x8000 | 0x4000, nx, ny, 0, 0)  # MOVE | ABSOLUTE | VIRTUALDESK
            return
        except Exception:
            pass
    ms.position = (x, y)


def apply_event(kind, data, held_keys, held_btns, press_time, mode):
    if kind == "move":
        move_cursor(data[0], data[1], mode)
    elif kind == "click":
        x, y, button, pressed = data
        move_cursor(x, y, mode)
        if pressed:
            time.sleep(SETTLE)
            ms.press(button)
            held_btns.add(button)
            press_time[button] = time.perf_counter()
        else:
            wait = MIN_HOLD - (time.perf_counter() - press_time.get(button, 0.0))
            if wait > 0:
                time.sleep(wait)
            ms.release(button)
            held_btns.discard(button)
    elif kind == "scroll":
        x, y, dx, dy = data
        move_cursor(x, y, mode)
        ms.scroll(dx, dy)
    elif kind == "key":
        key, pressed = data
        if pressed:
            kb.press(key)
            held_keys.add(key)
        else:
            kb.release(key)
            held_keys.discard(key)


def wait_until(target, stop):
    """Точное ожидание момента target (perf_counter). False, если нажали стоп."""
    while True:
        left = target - time.perf_counter()
        if left <= 0:
            return True
        if stop.is_set():
            return False
        if left > 0.003:
            time.sleep(min(left - 0.002, 0.05))


def play_events(events, repeats, speed, pause, stop, on_loop=None, mode=MODE_NORMAL):
    """Повторяет записанные события. repeats=0 — бесконечно. Возвращает число полных повторов."""
    held_keys, held_btns, press_time = set(), set(), {}
    loops = 0
    try:
        while not stop.is_set():
            start = time.perf_counter()  # все моменты считаются от начала круга: время не «плывёт»
            for t, kind, data in events:
                if not wait_until(start + t / speed, stop):
                    return loops
                try:
                    apply_event(kind, data, held_keys, held_btns, press_time, mode)
                except Exception:
                    pass  # одно неудавшееся действие не должно ломать весь повтор
            loops += 1
            if on_loop:
                on_loop(loops)
            if repeats and loops >= repeats:
                break
            if pause > 0 and stop.wait(pause):
                break
    finally:
        # что бы ни случилось — отпускаем всё, что осталось зажатым
        for key in list(held_keys):
            try:
                kb.release(key)
            except Exception:
                pass
        for btn in list(held_btns):
            try:
                ms.release(btn)
            except Exception:
                pass
    return loops


# ---------- вкладка «Зажим» ----------

class HoldTab:
    def __init__(self, root, parent):
        self.root = root
        self.holding = False
        self.arming = False
        self.targets = []

        frm = ttk.Frame(parent, padding=14)
        frm.pack(fill="both", expand=True)

        self.choices = []
        for i, default in enumerate(["W", NONE, NONE], start=1):
            ttk.Label(frm, text=f"Кнопка {i}").pack(anchor="w")
            box = ttk.Combobox(frm, values=ALL, state="readonly")
            box.set(default)
            box.pack(fill="x", pady=(2, 8))
            self.choices.append(box)

        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(2, 12))
        ttk.Label(row, text="Задержка перед зажатием, с").pack(side="left")
        self.delay = tk.IntVar(value=3)
        ttk.Spinbox(row, from_=0, to=30, width=4, textvariable=self.delay).pack(side="right")

        self.btn = ttk.Button(frm, text="Зажать (F8)", command=self.toggle)
        self.btn.pack(fill="x", ipady=8)

        self.status = ttk.Label(frm, text="Отпущено", foreground="#666")
        self.status.pack(pady=(12, 0))

    def selected(self):
        names = []
        for box in self.choices:
            name = box.get()
            if name != NONE and name not in names:
                names.append(name)
        return names

    def toggle(self):
        if self.holding:
            self.release()
            return
        if self.arming:
            return
        if not self.selected():
            self.status.config(text="Выберите хотя бы одну кнопку", foreground="#b00020")
            return
        try:
            wait = max(0, self.delay.get())
        except tk.TclError:
            wait = 3
        self.arming = True
        self.btn.config(state="disabled")
        if wait:
            self.countdown(wait)
        else:
            self.press()

    def countdown(self, left):
        if left == 0:
            self.press()
            return
        self.status.config(text=f"Зажму через {left} с — переключитесь в нужное окно", foreground="#666")
        self.root.after(1000, lambda: self.countdown(left - 1))

    def press(self):
        names = self.selected()
        self.targets = []
        for name in names:
            if name in MOUSE:
                target = MOUSE[name]
                ms.press(target)
            else:
                target = KEYS[name]
                kb.press(target)
            self.targets.append(target)
        self.arming = False
        self.holding = True
        self.btn.
