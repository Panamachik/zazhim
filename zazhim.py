"""Зажим — две программы в одном окне.
Вкладка «Зажим»: держит до трёх клавиш или кнопок мыши (F8).
Вкладка «Макрос»: записывает движения мыши и нажатия, потом повторяет (F9 запись, F10 пуск/стоп).
Вкладка «Курсор 2»: метка «второго курсора» на экране и мгновенный переброс мыши между двумя местами (F6 создать, F7 переключить).
"""
import ctypes
import os
import string
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback
from ctypes import wintypes
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
HOTKEYS = (keyboard.Key.f6, keyboard.Key.f7, keyboard.Key.f8, keyboard.Key.f9, keyboard.Key.f10)

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
    if n not in (6, 7, 8, 9, 10):  # F6–F10 заняты горячими клавишами
        KEYS[f"F{n}"] = getattr(keyboard.Key, f"f{n}")
for ch in string.ascii_lowercase + string.digits:
    KEYS[ch.upper()] = ch

MOUSE = {
    "Мышь: левая": mouse.Button.left,
    "Мышь: правая": mouse.Button.right,
    "Мышь: средняя": mouse.Button.middle,
}

ALL = [NONE] + list(MOUSE) + list(KEYS)


# ---------- запись ошибок: если что-то сломается, причина будет видна ----------

def error_log_path():
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "zazhim_error.txt")


def report(text, show=True):
    """Пишет ошибку в zazhim_error.txt и (если show) показывает окно с причиной."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    for path in (error_log_path(), os.path.join(tempfile.gettempdir(), "zazhim_error.txt")):
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"[{stamp}]\n{text}\n\n")
            break
        except Exception:
            continue
    if show:
        try:
            ctypes.windll.user32.MessageBoxW(
                0, "Программа не смогла запуститься. Сделайте скриншот этого окна.\n\n" + text[-700:],
                "Зажим — ошибка", 0x10)
        except Exception:
            pass


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


def jump_to(x, y):
    """Мгновенно и точно ставит курсор в (x, y): сначала как настоящее событие ввода, потом поправка до пикселя."""
    move_cursor(x, y, MODE_GAME)
    try:
        cur = ms.position
        if (int(cur[0]), int(cur[1])) != (x, y):
            ms.position = (x, y)
    except Exception:
        pass


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
        self.mode = tk.StringVar(value=MODE_NORMAL)
        for text, widget in (
            ("Повторов (0 — бесконечно)", ttk.Spinbox(frm, from_=0, to=99999, width=7, textvariable=self.repeats)),
            ("Скорость", ttk.Combobox(frm, values=["0.5x", "1x", "2x", "3x"], width=6,
                                      state="readonly", textvariable=self.speed)),
            ("Режим мыши", ttk.Combobox(frm, values=[MODE_NORMAL, MODE_GAME], width=10,
                                        state="readonly", textvariable=self.mode)),
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
            args=(list(self.events), repeats, speed, pause, self.stop_play, self.set_loops, self.mode.get()),
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


# ---------- вкладка «Курсор 2» ----------

class CursorTab:
    """В Windows настоящий курсор один. «Второй курсор» — это метка на экране:
    переключатель перебрасывает мышь на метку, а метку ставит туда, где мышь была."""

    SIZE = 36
    KEY = "#010101"  # этот цвет в окне метки прозрачный

    def __init__(self, root, parent):
        self.root = root
        self.marker = None
        self.canvas = None
        self.pos = None   # где сейчас стоит метка
        self.active = 1   # какой курсор сейчас «настоящий»
        self.arming = False

        frm = ttk.Frame(parent, padding=14)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, wraplength=300, foreground="#666",
                  text="Наведите мышь на нужное место и создайте второй курсор. "
                       "Потом переключатель перебрасывает мышь между двумя местами.").pack(anchor="w", pady=(0, 12))

        self.spawn_btn = ttk.Button(frm, text="Создать второй курсор (F6)", command=self.spawn_button)
        self.spawn_btn.pack(fill="x", ipady=8)
        self.swap_btn = ttk.Button(frm, text="Переключить курсор (F7)", command=self.swap)
        self.swap_btn.pack(fill="x", ipady=8, pady=8)
        self.remove_btn = ttk.Button(frm, text="Убрать второй курсор", command=self.remove)
        self.remove_btn.pack(fill="x", ipady=4)

        self.active_lbl = ttk.Label(frm, text="Второго курсора пока нет", foreground="#666")
        self.active_lbl.pack(pady=(14, 0))
        self.status = ttk.Label(frm, text="", foreground="#666", wraplength=300)
        self.status.pack(pady=(4, 0))

        self.keep_visible()

    # --- создание ---
    def spawn_button(self):
        if self.arming:
            return
        self.arming = True
        self.countdown(3)

    def countdown(self, left):
        if left == 0:
            self.arming = False
            self.spawn_now()
            return
        self.status.config(text=f"Курсор 2 появится через {left} с — наведите мышь на нужное место", foreground="#666")
        self.root.after(1000, lambda: self.countdown(left - 1))

    def spawn_now(self):
        x, y = ms.position
        self.pos = (int(x), int(y))
        self.active = 1
        self.show_marker()
        self.update_labels()
        self.status.config(text="Второй курсор создан. Переключать: F7", foreground="#0a7d33")

    # --- переключение ---
    def swap(self):
        if self.pos is None:
            self.status.config(text="Сначала создайте второй курсор (F6)", foreground="#b00020")
            return
        cx, cy = ms.position
        tx, ty = self.pos
        jump_to(tx, ty)
        self.pos = (int(cx), int(cy))  # метка встаёт туда, где мышь была
        self.active = 2 if self.active == 1 else 1
        self.show_marker()
        self.update_labels()
        self.status.config(text="", foreground="#666")

    def remove(self):
        if self.marker is not None:
            self.marker.destroy()
        self.marker = None
        self.canvas = None
        self.pos = None
        self.active = 1
        self.update_labels()
        self.status.config(text="Второй курсор убран", foreground="#666")

    def update_labels(self):
        if self.pos is None:
            self.active_lbl.config(text="Второго курсора пока нет", foreground="#666")
        else:
            self.active_lbl.config(text=f"Сейчас активен курсор {self.active}", foreground="#0a7d33")

    # --- метка на экране ---
    def show_marker(self):
        if self.pos is None:
            return
        if self.marker is None:
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            try:
                win.attributes("-transparentcolor", self.KEY)
            except tk.TclError:
                pass
            self.canvas = tk.Canvas(win, width=self.SIZE, height=self.SIZE, bg=self.KEY, highlightthickness=0)
            self.canvas.pack()
            self.marker = win
            win.update_idletasks()
            self.make_click_through(win)
        self.draw()
        x, y = self.pos
        self.marker.geometry(f"{self.SIZE}x{self.SIZE}+{x - 2}+{y - 2}")  # кончик стрелки точно в позиции

    def draw(self):
        c = self.canvas
        c.delete("all")
        c.create_polygon(2, 2, 2, 26, 8, 20, 13, 31, 18, 29, 13, 19, 21, 19,
                         fill="#e53935", outline="white", width=2)
        other = 2 if self.active == 1 else 1  # номер курсора, который стоит на метке
        c.create_oval(20, 20, 35, 35, fill="#e53935", outline="white", width=2)
        c.create_text(27, 27, text=str(other), fill="white", font=("Segoe UI", 9, "bold"))

    @staticmethod
    def make_click_through(win):
        """Метка не должна ловить клики и забирать фокус у игры."""
        try:
            u = ctypes.windll.user32
            u.GetParent.argtypes = [wintypes.HWND]
            u.GetParent.restype = wintypes.HWND
            u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            u.GetWindowLongW.restype = ctypes.c_long
            u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
            u.SetWindowLongW.restype = ctypes.c_long
            hwnd = u.GetParent(win.winfo_id()) or win.winfo_id()
            style = u.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
            # LAYERED | TRANSPARENT | TOOLWINDOW | NOACTIVATE
            u.SetWindowLongW(hwnd, -20, style | 0x80000 | 0x20 | 0x80 | 0x08000000)
        except Exception:
            pass

    def keep_visible(self):
        # если окно программы свернули (запись/повтор), метка всё равно должна быть видна
        if self.marker is not None:
            try:
                self.marker.deiconify()
                self.marker.attributes("-topmost", True)
            except Exception:
                pass
        self.root.after(500, self.keep_visible)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Зажим")
        root.geometry("360x490")
        root.resizable(False, False)
        root.attributes("-topmost", True)

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=6, pady=6)
        # каждая вкладка запускается отдельно: если одна сломается, остальные всё равно откроются
        self.hold = self.make_tab(nb, "Зажим", HoldTab)
        self.macro = self.make_tab(nb, "Макрос", MacroTab)
        self.cursor = self.make_tab(nb, "Курсор 2", CursorTab)

        # Глобальные горячие клавиши работают из любого окна
        self.hotkeys = None
        try:
            self.hotkeys = keyboard.GlobalHotKeys({
                "<f6>": self.hot(self.cursor, "spawn_now"),
                "<f7>": self.hot(self.cursor, "swap"),
                "<f8>": self.hot(self.hold, "toggle"),
                "<f9>": self.hot(self.macro, "toggle_record"),
                "<f10>": self.hot(self.macro, "toggle_play"),
            })
            self.hotkeys.start()
        except Exception:
            self.hotkeys = None
            report(traceback.format_exc(), show=False)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def make_tab(self, nb, title, cls):
        frame = ttk.Frame(nb)
        nb.add(frame, text=title)
        try:
            return cls(self.root, frame)
        except Exception as exc:
            report(traceback.format_exc(), show=False)
            for child in frame.winfo_children():
                child.destroy()
            ttk.Label(frame, text=f"Эта вкладка не запустилась:\n{exc}", wraplength=300,
                      foreground="#b00020").pack(padx=14, pady=20)
            return None

    def hot(self, tab, method):
        def run():
            if tab is not None:
                getattr(tab, method)()
        return lambda: self.root.after(0, run)

    def close(self):
        try:
            if self.hold is not None and self.hold.holding:
                self.hold.release()
            if self.macro is not None:
                self.macro.shutdown()
            if self.hotkeys is not None:
                self.hotkeys.stop()
        except Exception:
            pass
        try:
            ctypes.windll.winmm.timeEndPeriod(1)
        except Exception:
            pass
        self.root.destroy()


def main():
    try:
        root = tk.Tk()
        root.report_callback_exception = lambda exc, val, tb: report(
            "".join(traceback.format_exception(exc, val, tb)), show=False)
        App(root)
        root.mainloop()
    except Exception:
        report(traceback.format_exc())


if __name__ == "__main__":
    main()
