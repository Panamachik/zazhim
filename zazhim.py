"""Зажим — две программы в одном окне.
Вкладка «Зажим»: держит до трёх клавиш или кнопок мыши (F8).
Вкладка «Макрос»: записывает движения мыши и нажатия, потом повторяет (F9 запись, F10 пуск/стоп).
Вкладка «Курсор 2»: метка «второго курсора» на экране и мгновенный переброс мыши между двумя местами (F6 создать, F7 переключить).
"""
import ctypes
import string
import threading
import time
import tkinter as tk
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
