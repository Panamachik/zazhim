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

def apply_event(kind, data, held_keys, held_btns):
    if kind == "move":
        ms.position = data
    elif kind == "click":
        x, y, button, pressed = data
        ms.position = (x, y)
        if pressed:
            ms.press(button)
            held_btns.add(button)
        else:
            ms.release(button)
            held_btns.discard(button)
    elif kind == "scroll":
        x, y, dx, dy = data
        ms.position = (x, y)
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


def play_events(events, repeats, speed, pause, stop, on_loop=None):
    """Повторяет записанные события. repeats=0 — бесконечно. Возвращает число полных повторов."""
    held_keys, held_btns = set(), set()
    loops = 0
    try:
        while not stop.is_set():
            start = time.perf_counter()  # все моменты считаются от начала круга: время не «плывёт»
            for t, kind, data in events:
                if not wait_until(start + t / speed, stop):
                    return loops
                try:
                    apply_event(kind, data, held_keys, held_btns)
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
        self.btn.config(text="Отпустить (F8)", state="normal")
        self.status.config(text="Зажато: " + ", ".join(names), foreground="#0a7d33")

    def release(self):
        for target in self.targets:
            if isinstance(target, mouse.Button):
                ms.release(target)
            else:
                kb.release(target)
        self.targets = []
        self.holding = False
        self.btn.config(text="Зажать (F8)", state="normal")
        self.status.config(text="Отпущено", foreground="#666")


# ---------- вкладка «Макрос» ----------

class MacroTab:
    def __init__(self, root, parent):
        self.root = root
        self.state = "idle"  # idle / arming / recording / playing
        self.events = []
        self.duration = 0.0
        self.loops = 0
        self.stop_play = threading.Event()
        self.thread = None
        self.listeners = []
        self.t0 = 0.0
        self.down_keys = set()

        frm = ttk.Frame(parent, padding=14)
        frm.pack(fill="both", expand=True)

        self.rec_btn = ttk.Button(frm, text="Запись (F9)", command=lambda: self.toggle_record(True))
        self.rec_btn.pack(fill="x", ipady=8)
        self.play_btn = ttk.Button(frm, text="Воспроизвести (F10)", command=lambda: self.toggle_play(True))
        self.play_btn.pack(fill="x", ipady=8, pady=(8, 12))

        self.repeats = tk.IntVar(value=0)
        self.speed = tk.StringVar(value="1x")
        self.pause = tk.DoubleVar(value=0.0)
        for text, widget in (
            ("Повторов (0 — бесконечно)", ttk.Spinbox(frm, from_=0, to=99999, width=7, textvariable=self.repeats)),
            ("Скорость", ttk.Combobox(frm, values=["0.5x", "1x", "2x", "3x"], width=6,
                                      state="readonly", textvariable=self.speed)),
            ("Пауза между повторами, с", ttk.Spinbox(frm, from_=0, to=3600, increment=0.5, width=7,
                                                     textvariable=self.pause)),
        ):
            row = ttk.Frame(frm)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=text).pack(side="left")
            widget.pack(side="right")

        self.info = ttk.Label(frm, text="Записано: пока ничего", foreground="#666")
        self.info.pack(pady=(12, 0))
        self.status = ttk.Label(frm, text="Нажмите «Запись» или F9", foreground="#666", wraplength=300)
        self.status.pack(pady=(4, 0))

        self.tick()

    # --- запись ---
    def toggle_record(self, from_button=False):
        if self.state == "recording":
            self.stop_record()
        elif self.state == "idle":
            self.state = "arming"
            self.root.iconify()  # окно не должно попасть в запись и мешать при повторе
            self.root.after(900 if from_button else 0, self.begin_record)

    def begin_record(self):
        self.events = []
        self.down_keys = set()
        self.t0 = time.perf_counter()
        self.listeners = [
            mouse.Listener(on_move=self.on_move, on_click=self.on_click, on_scroll=self.on_scroll),
            keyboard.Listener(on_press=self.on_press, on_release=self.on_release),
        ]
        for lst in self.listeners:
            lst.start()
        self.state = "recording"
        self.refresh()

    def stop_record(self):
        for lst in self.listeners:
            lst.stop()
        self.listeners = []
        self.duration = self.events[-1][0] if self.events else 0.0
        self.state = "idle"
        self.root.deiconify()
        self.refresh()
        self.status.config(text="Запись сохранена. Нажмите «Воспроизвести» или F10", foreground="#0a7d33")

    def add(self, kind, data):
        self.events.append((time.perf_counter() - self.t0, kind, data))

    def on_move(self, x, y):
        self.add("move", (x, y))

    def on_click(self, x, y, button, pressed):
        self.add("click", (x, y, button, pressed))

    def on_scroll(self, x, y, dx, dy):
        self.add("scroll", (x, y, dx, dy))

    def on_press(self, key):
        if key in HOTKEYS or key in self.down_keys:  # хоткеи и автоповтор зажатой клавиши не пишем
            return
        self.down_keys.add(key)
        self.add("key", (key, True))

    def on_release(self, key):
        if key in HOTKEYS:
            return
        self.down_keys.discard(key)
        self.add("key", (key, False))

    # --- воспроизведение ---
    def toggle_play(self, from_button=False):
        if self.state == "playing":
            self.stop_play.set()
        elif self.state == "idle":
            if not self.events:
                self.status.config(text="Сначала запишите действия", foreground="#b00020")
                return
            self.state = "arming"
            self.root.iconify()
            self.root.after(1000 if from_button else 0, self.begin_play)

    def begin_play(self):
        try:
            repeats = max(0, int(self.repeats.get()))
        except (tk.TclError, ValueError):
            repeats = 0
        try:
            speed = float(self.speed.get().rstrip("x"))
        except ValueError:
            speed = 1.0
        try:
            pause = max(0.0, float(self.pause.get()))
        except (tk.TclError, ValueError):
            pause = 0.0
        self.stop_play.clear()
        self.loops = 0
        self.thread = threading.Thread(
            target=play_events,
            args=(list(self.events), repeats, speed, pause, self.stop_play, self.set_loops),
            daemon=True,
        )
        self.state = "playing"
        self.thread.start()
        self.refresh()

    def set_loops(self, n):
        self.loops = n

    # --- интерфейс ---
    def refresh(self):
        rec = self.state == "recording"
        play = self.state == "playing"
        self.rec_btn.config(text="Стоп записи (F9)" if rec else "Запись (F9)")
        self.play_btn.config(text="Стоп (F10)" if play else "Воспроизвести (F10)")
        if self.events and not rec:
            self.info.config(text=f"Записано: {len(self.events)} событий, {self.duration:.1f} с")

    def tick(self):
        if self.state == "recording":
            self.status.config(text=f"Идёт запись, событий: {len(self.events)}. Остановить: F9", foreground="#b00020")
        elif self.state == "playing":
            if self.thread is not None and not self.thread.is_alive():
                self.state = "idle"
                self.root.deiconify()
                self.refresh()
                self.status.config(text=f"Готово. Повторов: {self.loops}", foreground="#0a7d33")
            else:
                self.status.config(text=f"Идёт повтор, кругов: {self.loops}. Остановить: F10", foreground="#0a7d33")
        self.root.after(100, self.tick)

    def shutdown(self):
        self.stop_play.set()
        for lst in self.listeners:
            lst.stop()


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Зажим")
        root.geometry("360x440")
        root.resizable(False, False)
        root.attributes("-topmost", True)

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=6, pady=6)
        hold_frame = ttk.Frame(nb)
        macro_frame = ttk.Frame(nb)
        nb.add(hold_frame, text="Зажим")
        nb.add(macro_frame, text="Макрос")
        self.hold = HoldTab(root, hold_frame)
        self.macro = MacroTab(root, macro_frame)

        # Глобальные горячие клавиши работают из любого окна
        self.hotkeys = keyboard.GlobalHotKeys({
            "<f8>": lambda: root.after(0, self.hold.toggle),
            "<f9>": lambda: root.after(0, self.macro.toggle_record),
            "<f10>": lambda: root.after(0, self.macro.toggle_play),
        })
        self.hotkeys.start()
        root.protocol("WM_DELETE_WINDOW", self.close)

    def close(self):
        if self.hold.holding:
            self.hold.release()
        self.macro.shutdown()
        self.hotkeys.stop()
        try:
            ctypes.windll.winmm.timeEndPeriod(1)
        except Exception:
            pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
