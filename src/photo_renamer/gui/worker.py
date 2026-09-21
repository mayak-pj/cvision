"""Фоновая задача для интерфейса: долгое чтение файлов — в отдельном потоке.

Зачем: одно фото весит ~15 МБ, и по сети его чтение занимает секунды. Пока чтение идёт
в главном потоке, окно не реагирует на щелчки.

Правила, при которых это безопасно:
- поток всегда один и только один: пока задача не закончилась, новая не запускается;
- в потоке нет ни одного обращения к tkinter — он только читает файлы и считает;
- о ходе работы поток сообщает через очередь, а обновляет окно главный поток (after);
- обработка внутри задачи остаётся последовательной: одно фото за раз, как и раньше;
- кэш (SQLite) открывается внутри потока, потому что соединение принадлежит своему потоку.
"""

import queue
import threading
import traceback
from typing import Callable, Optional

POLL_MS = 30


class Cancelled(Exception):
    """Задача прервана пользователем."""


class Worker:
    def __init__(self, widget):
        self.widget = widget
        self._thread: Optional[threading.Thread] = None
        self._queue: "queue.Queue" = queue.Queue()
        self._cancel = threading.Event()
        self._result = None
        self._error: Optional[BaseException] = None
        self._traceback = ""
        self._on_message: Optional[Callable] = None
        self._on_done: Optional[Callable] = None
        self._polling = False

    # --- запуск и остановка -------------------------------------------------------------

    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, job: Callable, on_message: Callable, on_done: Callable) -> bool:
        """job(post, cancelled) выполняется в потоке и возвращает результат.

        post(сообщение) — отдать сообщение главному потоку; cancelled() — проверить отмену
        (удобно вызывать в обратных вызовах прогресса и поднимать Cancelled).
        on_message(сообщение) и on_done(результат, ошибка) вызываются в главном потоке.
        """
        if self.busy():
            return False
        self._queue = queue.Queue()
        self._cancel.clear()
        self._result = None
        self._error = None
        self._traceback = ""
        self._on_message = on_message
        self._on_done = on_done

        def run():
            try:
                self._result = job(self._queue.put, self._cancel.is_set)
            except BaseException as exc:  # ошибка не должна «проглатываться» потоком
                self._error = exc
                self._traceback = traceback.format_exc()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        if not self._polling:
            self._polling = True
            self.widget.after(POLL_MS, self._poll)
        return True

    def cancel(self) -> None:
        self._cancel.set()

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # --- приём сообщений в главном потоке -------------------------------------------------

    def _drain(self) -> None:
        while True:
            try:
                message = self._queue.get_nowait()
            except queue.Empty:
                return
            if self._on_message:
                self._on_message(message)

    def _poll(self) -> None:
        self._drain()
        if self.busy():
            self.widget.after(POLL_MS, self._poll)
            return
        self._polling = False
        self._drain()
        on_done, self._on_done = self._on_done, None
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=0)
        if on_done:
            on_done(self._result, self._error)

    def wait(self, timeout: float = 600.0) -> None:
        """Ждёт окончания задачи, продолжая обновлять окно. Нужно для тестов и закрытия окна."""
        import time

        deadline = time.time() + timeout
        while self.busy() and time.time() < deadline:
            self.widget.update()
            time.sleep(0.005)
        self.widget.update()
        if self._polling:
            self._poll()
