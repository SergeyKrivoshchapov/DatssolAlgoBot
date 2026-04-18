import sys
import requests
import time
import json
import os
import select
import subprocess
from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QPushButton, QTextEdit,
                             QTableWidget, QTableWidgetItem, QTabWidget, QSplitter,
                             QHeaderView, QLineEdit, QScrollArea, QGridLayout)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import (QPainter, QColor, QPen, QFont, QMouseEvent,
                         QWheelEvent, QAction, QKeySequence, QPalette)

class CoordinatorThread(QThread):
    arena_updated = pyqtSignal(object)
    logs_updated = pyqtSignal(object)
    command_sent = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, token: str, bot_python: str, bot_script: str, logs_every: int = 10, parent=None):
        super().__init__(parent)
        self.token = token
        self.logs_every = max(0, int(logs_every))
        self._stop = False
        self._session = requests.Session()
        self._last_turn = None
        self._last_logs_turn = None

        self._bot_python = bot_python
        self._bot_script = bot_script
        self._bot_proc = None

    def stop(self):
        self._stop = True
        if self._bot_proc:
            try:
                self._bot_proc.terminate()
                self._bot_proc.wait(timeout=1)
            except:
                self._bot_proc.kill()

    def _headers(self):
        return {'X-Auth-Token': self.token, 'accept': 'application/json'}

    def _get(self, endpoint: str):
        r = self._session.get(f'https://games-test.datsteam.dev{endpoint}', headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, endpoint: str, payload: dict):
        r = self._session.post(f'https://games-test.datsteam.dev{endpoint}', headers=self._headers(), json=payload, timeout=10)
        r.raise_for_status()
        return r.json()

    def _ensure_bot(self):
        if self._bot_proc is not None and self._bot_proc.poll() is None:
            self.error.emit("Бот завершился, перезапуск...")
        self._bot_proc = subprocess.Popen(
            [self._bot_python, self._bot_script, "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

    def _bot_decide(self, arena: dict) -> dict | None:
        self._ensure_bot()

        try:
            self._bot_proc.stdin.write(json.dumps(arena) + "\n")
            self._bot_proc.stdin.flush()
        except Exception:
            return None

        deadline = time.monotonic() + 0.25

        while time.monotonic() < deadline:
            ready, _, _ = select.select([self._bot_proc.stdout], [], [], 0.05)
            if ready:
                line = self._bot_proc.stdout.readline()
                if not line:
                    break
                try:
                    payload = json.loads(line.strip())
                    if isinstance(payload, dict):
                        return payload
                except:
                    continue

        return None


    def run(self):
        while not self._stop:
            # 1. Делаем GET и запоминаем ВРЕМЯ получения ответа
            request_start = time.monotonic()

            try:
                arena = self._get('/api/arena')
            except Exception as e:
                self.error.emit(str(e))
                time.sleep(1.0)
                continue

            request_end = time.monotonic()
            self.arena_updated.emit(arena)

            turn_no = arena.get('turnNo')
            try:
                turn_i = int(turn_no)
            except Exception:
                turn_i = None

            # 2. Если ход сменился — отправляем команду
            if turn_i is not None and turn_i != self._last_turn:
                self._last_turn = turn_i

                # Логи раз в 10 ходов
                if self.logs_every > 0 and (
                        self._last_logs_turn is None or (turn_i - self._last_logs_turn) >= self.logs_every):
                    self._last_logs_turn = turn_i
                    try:
                        logs = self._get('/api/logs')
                        if isinstance(logs, list):
                            self.logs_updated.emit(logs)
                    except Exception:
                        pass

                payload = self._bot_decide(arena)
                if payload is not None:
                    try:
                        resp = self._post('/api/command', payload)
                        self.command_sent.emit(resp)
                    except Exception as e:
                        self.error.emit(str(e))

            # 3. КЛЮЧЕВОЙ МОМЕНТ: спим до начала следующего хода + запас
            next_turn_in = arena.get('nextTurnIn', 1.0)
            try:
                next_turn_in = float(next_turn_in)
            except Exception:
                next_turn_in = 1.0

            # Время, когда мы получили ответ
            # Спим ровно next_turn_in + 0.05 секунд от МОМЕНТА ПОЛУЧЕНИЯ ОТВЕТА
            sleep_time = next_turn_in + 0.05

            # Вычитаем время, которое уже прошло с момента получения ответа
            elapsed = time.monotonic() - request_end
            actual_sleep = max(0.01, sleep_time - elapsed)

            time.sleep(actual_sleep)


class GameAPIWorker(QThread):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, token, endpoint):
        super().__init__()
        self.token = token
        self.endpoint = endpoint

    def run(self):
        try:
            headers = {'X-Auth-Token': self.token, 'accept': 'application/json'}
            response = requests.get(
                f'https://games-test.datsteam.dev{self.endpoint}',
                headers=headers,
                timeout=10
            )
            if response.status_code == 200:
                self.finished.emit(response.json())
            else:
                self.error.emit(f"HTTP {response.status_code}: {response.text[:200]}")
        except Exception as e:
            self.error.emit(str(e))


class GameMapWidget(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(600, 600)
        self.game_state = None

        self.zoom_level = 1.0
        self.min_zoom = 0.3
        self.max_zoom = 5.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.last_mouse_pos = None
        self.cell_size = 8

        self.selected_position = None

        self._hover_cell = None
        self._is_panning = False

        self.colors = {
            'desert': QColor(34, 26, 18),
            'mountain': QColor(150, 160, 175),
            'own_plantation': QColor(0, 200, 90),
            'main_plantation': QColor(255, 215, 0),
            'enemy_plantation': QColor(220, 60, 60),
            'construction': QColor(0, 140, 255),
            'beaver': QColor(255, 145, 0),
            'oasis': QColor(0, 190, 150),
            'bonus_cell': QColor(160, 90, 235),
            'grid': QColor(52, 52, 64),
            'sandstorm': QColor(240, 200, 80, 120),
            'background': QColor(18, 18, 24),
            'selection': QColor(120, 210, 255),
            'hover': QColor(255, 255, 255, 40),
        }

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_game_state(self, state):
        self.game_state = state
        self.update()

    def center_on_cell(self, x: int, y: int, zoom_level: float | None = None):
        if not self.game_state:
            return

        size = self.game_state.get('size', [100, 100])
        map_width, map_height = size
        map_width = max(1, int(map_width))
        map_height = max(1, int(map_height))

        if zoom_level is not None:
            self.zoom_level = max(self.min_zoom, min(self.max_zoom, float(zoom_level)))

        scaled_cell_size = self.scaled_cell_size()
        map_pixel_width = map_width * scaled_cell_size
        map_pixel_height = map_height * scaled_cell_size

        base_offset_x = int((self.width() - map_pixel_width) // 2)
        base_offset_y = int((self.height() - map_pixel_height) // 2)

        desired_x = (self.width() / 2) - (base_offset_x + (x + 0.5) * scaled_cell_size)
        desired_y = (self.height() / 2) - (base_offset_y + (y + 0.5) * scaled_cell_size)

        self.pan_x = float(desired_x)
        self.pan_y = float(desired_y)
        self.update()

    def scaled_cell_size(self) -> int:
        return max(2, int(self.cell_size * float(self.zoom_level)))

    @staticmethod
    def _pos_to_tuple(pos):
        if pos is None:
            return None
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                return int(pos[0]), int(pos[1])
            except Exception:
                return None
        if isinstance(pos, dict) and 'x' in pos and 'y' in pos:
            try:
                return int(pos['x']), int(pos['y'])
            except Exception:
                return None
        return None

    def enemies(self):
        if not self.game_state:
            return []
        candidates = []
        for key in ('enemy', 'enemies', 'enemyPlantations', 'opponents'):
            val = self.game_state.get(key)
            if isinstance(val, list):
                candidates.extend(val)
        out = []
        for e in candidates:
            if not isinstance(e, dict):
                continue
            p = self._pos_to_tuple(e.get('position') or e.get('pos') or e.get('coordinates'))
            if not p:
                continue
            hp = e.get('hp', e.get('health', 0))
            try:
                hp = int(hp)
            except Exception:
                hp = 0
            out.append({'position': p, 'hp': hp, 'raw': e})
        return out

    def wheelEvent(self, event: QWheelEvent):
        if not self.game_state:
            return

        anchor = None
        if self.selected_position is not None and isinstance(self.selected_position, (list, tuple)) and len(self.selected_position) == 2:
            try:
                anchor = (int(self.selected_position[0]), int(self.selected_position[1]))
            except Exception:
                anchor = None

        if anchor is None:
            cell = self._screen_to_cell(event.position().x(), event.position().y())
            if cell is not None:
                ax, ay = cell
                size = self.game_state.get('size', [0, 0])
                try:
                    mw, mh = int(size[0]), int(size[1])
                except Exception:
                    mw, mh = 0, 0
                if 0 <= ax < mw and 0 <= ay < mh:
                    anchor = (ax, ay)

        old_zoom = float(self.zoom_level)
        delta = event.angleDelta().y() / 1200.0
        new_zoom = max(self.min_zoom, min(self.max_zoom, old_zoom + delta))
        if new_zoom == old_zoom:
            return

        if anchor is not None:
            ax, ay = anchor
            old_scs = max(2, int(self.cell_size * old_zoom))
            new_scs = max(2, int(self.cell_size * new_zoom))

            size = self.game_state.get('size', [100, 100])
            map_width, map_height = size
            map_width = max(1, int(map_width))
            map_height = max(1, int(map_height))

            old_map_pixel_width = map_width * old_scs
            old_map_pixel_height = map_height * old_scs
            old_offset_x = int((self.width() - old_map_pixel_width) // 2 + self.pan_x)
            old_offset_y = int((self.height() - old_map_pixel_height) // 2 + self.pan_y)

            screen_x = old_offset_x + (ax + 0.5) * old_scs
            screen_y = old_offset_y + (ay + 0.5) * old_scs

            new_map_pixel_width = map_width * new_scs
            new_map_pixel_height = map_height * new_scs
            new_base_x = int((self.width() - new_map_pixel_width) // 2)
            new_base_y = int((self.height() - new_map_pixel_height) // 2)

            self.zoom_level = float(new_zoom)
            self.pan_x = float(screen_x - (new_base_x + (ax + 0.5) * new_scs))
            self.pan_y = float(screen_y - (new_base_y + (ay + 0.5) * new_scs))
        else:
            self.zoom_level = float(new_zoom)

        if hasattr(self.parent(), 'on_zoom_changed'):
            self.parent().on_zoom_changed(self.zoom_level)
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if not self.game_state:
            return

        pos = event.position()
        if event.button() == Qt.MouseButton.LeftButton:
            cell_pos = self._screen_to_cell(pos.x(), pos.y())
            if cell_pos:
                x, y = cell_pos
                size = self.game_state.get('size', [100, 100])
                if 0 <= x < size[0] and 0 <= y < size[1]:
                    self.selected_position = [x, y]
                    self.update()
                    if hasattr(self.parent(), 'on_cell_selected'):
                        self.parent().on_cell_selected(x, y)

    def mouseReleaseEvent(self, event: QMouseEvent):
        return

    def mouseMoveEvent(self, event: QMouseEvent):
        cell_pos = self._screen_to_cell(event.position().x(), event.position().y())
        if cell_pos != self._hover_cell:
            self._hover_cell = cell_pos
            if hasattr(self.parent(), 'on_hover_cell') and cell_pos is not None:
                self.parent().on_hover_cell(cell_pos[0], cell_pos[1])
            self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        text = event.text()

        scs = self.scaled_cell_size()
        step = max(8, int(scs))

        dx = 0
        dy = 0

        if text == 'h' or key == Qt.Key.Key_Left:
            dx = step
        elif text == 'l' or key == Qt.Key.Key_Right:
            dx = -step
        elif text == 'k' or key == Qt.Key.Key_Up:
            dy = step
        elif text == 'j' or key == Qt.Key.Key_Down:
            dy = -step

        if dx != 0 or dy != 0:
            self.pan_x += float(dx)
            self.pan_y += float(dy)
            self.update()
            event.accept()
            return

        super().keyPressEvent(event)

    def _screen_to_cell(self, screen_x, screen_y):
        if not self.game_state:
            return None

        size = self.game_state.get('size', [100, 100])
        map_width, map_height = size
        map_width = max(1, int(map_width))
        map_height = max(1, int(map_height))

        scaled_cell_size = self.scaled_cell_size()
        map_pixel_width = map_width * scaled_cell_size
        map_pixel_height = map_height * scaled_cell_size

        offset_x = int((self.width() - map_pixel_width) // 2 + self.pan_x)
        offset_y = int((self.height() - map_pixel_height) // 2 + self.pan_y)

        cell_x = int((screen_x - offset_x) // scaled_cell_size)
        cell_y = int((screen_y - offset_y) // scaled_cell_size)

        return cell_x, cell_y

    def paintEvent(self, event):
        if not self.game_state:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(0, 0, self.width(), self.height(), self.colors['background'])

        size = self.game_state.get('size', [100, 100])
        map_width, map_height = size
        map_width = max(1, int(map_width))
        map_height = max(1, int(map_height))

        scaled_cell_size = self.scaled_cell_size()

        map_pixel_width = map_width * scaled_cell_size
        map_pixel_height = map_height * scaled_cell_size

        offset_x = int((self.width() - map_pixel_width) // 2 + self.pan_x)
        offset_y = int((self.height() - map_pixel_height) // 2 + self.pan_y)

        painter.fillRect(offset_x, offset_y, map_pixel_width, map_pixel_height, self.colors['desert'])

        for x in range(0, map_width, 7):
            for y in range(0, map_height, 7):
                painter.fillRect(offset_x + x * scaled_cell_size,
                                 offset_y + y * scaled_cell_size,
                                 scaled_cell_size, scaled_cell_size,
                                 self.colors['bonus_cell'])

        for mountain in self.game_state.get('mountains', []):
            x, y = mountain
            painter.fillRect(offset_x + x * scaled_cell_size,
                             offset_y + y * scaled_cell_size,
                             scaled_cell_size, scaled_cell_size,
                             self.colors['mountain'])

        for cell in self.game_state.get('cells', []):
            x, y = cell['position']
            progress = cell.get('terraformationProgress', 0)
            if progress > 0:
                intensity = 0.3 + (progress / 100) * 0.7
                color = QColor(
                    int(self.colors['oasis'].red() * intensity),
                    int(self.colors['oasis'].green() * intensity),
                    int(self.colors['oasis'].blue() * intensity)
                )
                painter.fillRect(offset_x + x * scaled_cell_size,
                                 offset_y + y * scaled_cell_size,
                                 scaled_cell_size, scaled_cell_size,
                                 color)

        for storm in self.game_state.get('meteoForecasts', []):
            if storm.get('kind') == 'sandstorm' and not storm.get('forming', True):
                x, y = storm['position']
                radius = storm.get('radius', 1)
                painter.fillRect(offset_x + (x - radius) * scaled_cell_size,
                                 offset_y + (y - radius) * scaled_cell_size,
                                 (radius * 2 + 1) * scaled_cell_size,
                                 (radius * 2 + 1) * scaled_cell_size,
                                 self.colors['sandstorm'])
                painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
                painter.drawRect(offset_x + (x - radius) * scaled_cell_size,
                                 offset_y + (y - radius) * scaled_cell_size,
                                 (radius * 2 + 1) * scaled_cell_size,
                                 (radius * 2 + 1) * scaled_cell_size)

        for const in self.game_state.get('construction', []):
            x, y = const['position']
            progress = const.get('progress', 0)
            painter.fillRect(offset_x + x * scaled_cell_size,
                             offset_y + y * scaled_cell_size,
                             scaled_cell_size, scaled_cell_size,
                             self.colors['construction'])
            if scaled_cell_size >= 8:
                painter.setPen(QPen(Qt.GlobalColor.white, 1))
                font = QFont('Segoe UI', max(6, scaled_cell_size // 3))
                painter.setFont(font)
                painter.drawText(offset_x + x * scaled_cell_size + 2,
                                 offset_y + y * scaled_cell_size + scaled_cell_size - 3,
                                 f"{progress}")

        for enemy in self.enemies():
            x, y = enemy['position']
            hp = enemy.get('hp', 0)
            painter.fillRect(offset_x + x * scaled_cell_size,
                             offset_y + y * scaled_cell_size,
                             scaled_cell_size, scaled_cell_size,
                             self.colors['enemy_plantation'])
            painter.setPen(QPen(Qt.GlobalColor.white, 1))
            painter.drawRect(offset_x + x * scaled_cell_size,
                             offset_y + y * scaled_cell_size,
                             scaled_cell_size, scaled_cell_size)
            painter.drawLine(offset_x + x * scaled_cell_size,
                             offset_y + y * scaled_cell_size,
                             offset_x + x * scaled_cell_size + scaled_cell_size,
                             offset_y + y * scaled_cell_size + scaled_cell_size)
            painter.drawLine(offset_x + x * scaled_cell_size + scaled_cell_size,
                             offset_y + y * scaled_cell_size,
                             offset_x + x * scaled_cell_size,
                             offset_y + y * scaled_cell_size + scaled_cell_size)
            if scaled_cell_size >= 8:
                painter.setPen(QPen(Qt.GlobalColor.white, 1))
                font = QFont('Segoe UI', max(6, scaled_cell_size // 3))
                painter.setFont(font)
                painter.drawText(offset_x + x * scaled_cell_size + 2,
                                 offset_y + y * scaled_cell_size + scaled_cell_size - 3,
                                 str(hp))

        for beaver in self.game_state.get('beavers', []):
            x, y = beaver['position']
            hp = beaver.get('hp', 0)
            painter.fillRect(offset_x + x * scaled_cell_size,
                             offset_y + y * scaled_cell_size,
                             scaled_cell_size, scaled_cell_size,
                             self.colors['beaver'])
            if scaled_cell_size >= 8:
                painter.setPen(QPen(Qt.GlobalColor.white, 1))
                font = QFont('Segoe UI', max(6, scaled_cell_size // 3))
                painter.setFont(font)
                painter.drawText(offset_x + x * scaled_cell_size + 2,
                                 offset_y + y * scaled_cell_size + scaled_cell_size - 3,
                                 str(hp))

        for plant in self.game_state.get('plantations', []):
            x, y = plant['position']
            hp = plant.get('hp', 0)
            is_main = plant.get('isMain', False)

            color = self.colors['main_plantation'] if is_main else self.colors['own_plantation']
            painter.fillRect(offset_x + x * scaled_cell_size,
                             offset_y + y * scaled_cell_size,
                             scaled_cell_size, scaled_cell_size,
                             color)

            if plant.get('isIsolated', False):
                painter.setPen(QPen(Qt.GlobalColor.red, 2))
                painter.drawRect(offset_x + x * scaled_cell_size,
                                 offset_y + y * scaled_cell_size,
                                 scaled_cell_size, scaled_cell_size)

            if scaled_cell_size >= 8:
                painter.setPen(QPen(Qt.GlobalColor.black, 1))
                font = QFont('Segoe UI', max(6, scaled_cell_size // 3))
                painter.setFont(font)
                painter.drawText(offset_x + x * scaled_cell_size + 2,
                                 offset_y + y * scaled_cell_size + scaled_cell_size - 3,
                                 str(hp))

        if scaled_cell_size >= 4:
            painter.setPen(QPen(self.colors['grid'], 1))
            for x in range(map_width + 1):
                painter.drawLine(offset_x + x * scaled_cell_size, offset_y,
                                 offset_x + x * scaled_cell_size, offset_y + map_pixel_height)
            for y in range(map_height + 1):
                painter.drawLine(offset_x, offset_y + y * scaled_cell_size,
                                 offset_x + map_pixel_width, offset_y + y * scaled_cell_size)

        if self.selected_position:
            x, y = self.selected_position
            painter.setPen(QPen(self.colors['selection'], 3))
            painter.drawRect(offset_x + x * scaled_cell_size,
                             offset_y + y * scaled_cell_size,
                             scaled_cell_size, scaled_cell_size)

        if self._hover_cell and scaled_cell_size >= 3:
            hx, hy = self._hover_cell
            if 0 <= hx < map_width and 0 <= hy < map_height:
                painter.fillRect(offset_x + hx * scaled_cell_size,
                                 offset_y + hy * scaled_cell_size,
                                 scaled_cell_size, scaled_cell_size,
                                 self.colors['hover'])

    def reset_view(self):
        self.zoom_level = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0

        if not self.game_state:
            self.update()
            return

        main_pos = None
        for p in self.game_state.get('plantations', []) or []:
            if isinstance(p, dict) and p.get('isMain'):
                main_pos = self._pos_to_tuple(p.get('position'))
                if main_pos is not None:
                    break

        if main_pos is not None:
            self.center_on_cell(main_pos[0], main_pos[1])
            return

        if self.selected_position is not None and isinstance(self.selected_position, (list, tuple)) and len(self.selected_position) == 2:
            try:
                self.center_on_cell(int(self.selected_position[0]), int(self.selected_position[1]))
                return
            except Exception:
                pass

        self.update()


class LegendWidget(QWidget):

    def __init__(self, colors: dict):
        super().__init__()
        layout = QGridLayout(self)
        layout.setSpacing(5)
        layout.setContentsMargins(10, 5, 10, 5)

        legend_items = [
            ('Пустыня', colors['desert']),
            ('Горы', colors['mountain']),
            ('Своя плантация', colors['own_plantation']),
            ('Центр управления', colors['main_plantation']),
            ('Вражеская плантация', colors['enemy_plantation']),
            ('Стройка', colors['construction']),
            ('Логово бобров', colors['beaver']),
            ('Оазис', colors['oasis']),
            ('Бонусная клетка', colors['bonus_cell']),
            ('Песчаная буря', colors['sandstorm']),
        ]

        row, col = 0, 0
        for text, color in legend_items:
            color_label = QLabel()
            color_label.setFixedSize(20, 20)
            color_label.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #555;")

            text_label = QLabel(text)
            text_label.setStyleSheet("color: #ccc; font-size: 10px;")

            layout.addWidget(color_label, row, col * 2)
            layout.addWidget(text_label, row, col * 2 + 1)

            col += 1
            if col >= 3:
                col = 0
                row += 1


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.token = None
        self.api_worker = None
        self.logs_worker = None
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.fetch_arena)

        self._last_arena_received_mono = None
        self._last_turn_no = None
        self._last_logs_turn = None

        self._server_turn_deadline_mono = None
        self._poll_until_turn_change = False
        self._poll_interval_ms = 75
        self._did_initial_focus = False

        self._coord = None

        self.init_ui()
        self.apply_dark_theme()

    def init_ui(self):
        self.setWindowTitle('DatsSol — Визуализатор')
        self.setGeometry(100, 100, 1400, 900)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(3)

        top_panel = QWidget()
        top_layout = QHBoxLayout(top_panel)
        top_layout.setContentsMargins(5, 2, 5, 2)

        top_layout.addWidget(QLabel('Токен:'))
        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText('Вставьте токен...')
        self.token_input.setMaximumWidth(400)
        self.token_input.returnPressed.connect(self.connect_to_game)
        top_layout.addWidget(self.token_input)

        self.connect_btn = QPushButton('Подключиться')
        self.connect_btn.clicked.connect(self.connect_to_game)
        top_layout.addWidget(self.connect_btn)

        top_layout.addStretch()

        self.info_label = QLabel('Не подключено')
        font = QFont('Segoe UI', 11)
        font.setBold(True)
        self.info_label.setFont(font)
        top_layout.addWidget(self.info_label)

        main_layout.addWidget(top_panel)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.map_widget = GameMapWidget(self)
        left_layout.addWidget(self.map_widget)

        map_controls = QWidget()
        map_controls_layout = QHBoxLayout(map_controls)
        map_controls_layout.setContentsMargins(5, 2, 5, 2)

        self.zoom_label = QLabel('Зум: 1.0x')
        map_controls_layout.addWidget(self.zoom_label)

        reset_view_btn = QPushButton('Сбросить вид')
        reset_view_btn.clicked.connect(self.map_widget.reset_view)
        map_controls_layout.addWidget(reset_view_btn)

        map_controls_layout.addStretch()
        map_controls_layout.addWidget(QLabel('hjkl/стрелки — перемещение | Колёсико — зум по выбранной клетке'))

        left_layout.addWidget(map_controls)

        legend = LegendWidget(self.map_widget.colors)
        left_layout.addWidget(legend)

        main_splitter.addWidget(left_panel)

        right_panel = QTabWidget()
        right_panel.setMaximumWidth(450)

        self.plantations_table = QTableWidget()
        self.plantations_table.setColumnCount(5)
        self.plantations_table.setHorizontalHeaderLabels(['X', 'Y', 'HP', 'ЦУ', 'Изол.'])
        self.plantations_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        right_panel.addTab(self.plantations_table, 'Плантации')

        self.upgrades_table = QTableWidget()
        self.upgrades_table.setColumnCount(3)
        self.upgrades_table.setHorizontalHeaderLabels(['Название', 'Уровень', 'Очки'])
        self.upgrades_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        right_panel.addTab(self.upgrades_table, 'Апгрейды')

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        right_panel.addTab(self.log_text, 'Логи')

        self.cell_info = QTextEdit()
        self.cell_info.setReadOnly(True)
        right_panel.addTab(self.cell_info, 'Инфо о клетке')

        main_splitter.addWidget(right_panel)
        main_splitter.setSizes([950, 450])

        main_layout.addWidget(main_splitter)

        self.status_label = QLabel('Ожидание подключения...')
        main_layout.addWidget(self.status_label)

        zoom_in_action = QAction('Zoom In', self)
        zoom_in_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        zoom_in_action.triggered.connect(self.zoom_in)
        self.addAction(zoom_in_action)

        zoom_out_action = QAction('Zoom Out', self)
        zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        zoom_out_action.triggered.connect(self.zoom_out)
        self.addAction(zoom_out_action)

    def apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #1a1a20;
                color: #e0e0e0;
            }
            QLabel {
                color: #ccc;
            }
            QLineEdit, QTextEdit, QTableWidget {
                background-color: #2a2a30;
                color: #e0e0e0;
                border: 1px solid #444;
                border-radius: 3px;
            }
            QPushButton {
                background-color: #3a3a45;
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 5px 10px;
            }
            QPushButton:hover {
                background-color: #4a4a55;
            }
            QPushButton:pressed {
                background-color: #2a2a35;
            }
            QTabWidget::pane {
                border: 1px solid #444;
                background-color: #1e1e25;
            }
            QTabBar::tab {
                background-color: #2a2a30;
                color: #ccc;
                padding: 6px 12px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #3a3a45;
                color: #fff;
            }
            QTabBar::tab:hover {
                background-color: #353540;
            }
            QHeaderView::section {
                background-color: #2a2a30;
                color: #e0e0e0;
                border: 1px solid #444;
                padding: 4px;
            }
            QTableWidget {
                gridline-color: #444;
            }
            QScrollBar:vertical, QScrollBar:horizontal {
                background-color: #2a2a30;
                border: 1px solid #444;
            }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
                background-color: #555;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
                background-color: #666;
            }
        """)

    def connect_to_game(self):
        self.token = self.token_input.text().strip()
        if self.token:
            self.status_label.setText('Подключение...')
            self.update_timer.stop()
            self._start_coordinator()
    def _start_coordinator(self):
        if self._coord is not None:
            return

        bot_script = os.path.join(os.path.dirname(__file__), 'bot.py')
        py = sys.executable
        self._coord = CoordinatorThread(self.token, py, bot_script, logs_every=10)
        self._coord.arena_updated.connect(self.on_arena_received)
        self._coord.logs_updated.connect(self.on_logs_received)
        self._coord.command_sent.connect(self.on_command_sent)
        self._coord.error.connect(self.on_api_error)
        self._coord.start()

    def on_command_sent(self, resp):
        if isinstance(resp, dict):
            errs = resp.get('errors')
            if errs:
                self.log_text.append(str(resp))
            else:
                self.status_label.setText('Команда отправлена')

    def fetch_arena(self):
        return

    def fetch_logs(self):
        if not self.token:
            return

        if self.logs_worker and self.logs_worker.isRunning():
            return

        self.logs_worker = GameAPIWorker(self.token, '/api/logs')
        self.logs_worker.finished.connect(self.on_logs_received)
        self.logs_worker.error.connect(self.on_api_error)
        self.logs_worker.start()

    def on_arena_received(self, data):
        if isinstance(data, list):
            self.on_api_error(f'Некорректный ответ /api/arena: ожидался объект, пришёл список (len={len(data)})')
            return
        if not isinstance(data, dict):
            self.on_api_error(f'Некорректный ответ /api/arena: {type(data).__name__}')
            return
        if 'errors' in data and data.get('errors'):
            self.on_api_error(' | '.join(str(e) for e in data.get('errors', [])))
            return
        self.map_widget.set_game_state(data)

        turn_no = data.get('turnNo', 0)
        next_turn = data.get('nextTurnIn', 1)
        try:
            next_turn = float(next_turn)
        except Exception:
            next_turn = 1.0

        if next_turn < 0:
            next_turn = 0.0

        own_cnt = len(data.get('plantations', []) or [])
        enemy_cnt = len(data.get('enemy', []) or [])
        beaver_cnt = len(data.get('beavers', []) or [])
        self.info_label.setText(f'Ход {turn_no} | {next_turn:.2f}с | Свои: {own_cnt} | Враги: {enemy_cnt} | Бобры: {beaver_cnt}')
        self.status_label.setText(f'Обновлено (ход {turn_no})')

        self.update_plantations_table(data.get('plantations', []))
        self.update_upgrades_table(data.get('plantationUpgrades', {}))

        now_mono = time.monotonic()
        self._last_arena_received_mono = now_mono
        self._last_turn_no = turn_no

        turn_no_int = None
        try:
            turn_no_int = int(turn_no)
        except Exception:
            turn_no_int = None

        if turn_no_int is not None and (self._last_logs_turn is None or self._last_logs_turn != turn_no_int):
            self._last_logs_turn = turn_no_int
            self.fetch_logs()

        main_pos = None
        for p in data.get('plantations', []) or []:
            if isinstance(p, dict) and p.get('isMain'):
                main_pos = self.map_widget._pos_to_tuple(p.get('position'))
                break
        if main_pos and not self._did_initial_focus:
            x0, y0 = main_pos
            view_cells = 42
            zoom = min(self.map_widget.max_zoom,
                       max(self.map_widget.min_zoom,
                           min(self.map_widget.width(), self.map_widget.height()) / (view_cells * self.map_widget.cell_size)))
            self.map_widget.center_on_cell(x0, y0, zoom_level=zoom)
            self._did_initial_focus = True

        self._server_turn_deadline_mono = now_mono + next_turn
        self._poll_until_turn_change = True
        self._schedule_next_fetch()

    def on_logs_received(self, logs):
        self.log_text.clear()
        if isinstance(logs, dict):
            if logs.get('errors'):
                self.log_text.append('❌ ' + ' | '.join(str(e) for e in logs.get('errors', [])))
            else:
                self.log_text.append(f'Некорректный ответ /api/logs: {logs}')
            return
        if not isinstance(logs, list):
            self.log_text.append(f'Некорректный ответ /api/logs: {type(logs).__name__}')
            return
        for log in logs[-30:]:
            if isinstance(log, dict):
                self.log_text.append(f"[{log.get('time', '')}] {log.get('message', '')}")
        self._schedule_next_fetch()

    def on_api_error(self, error_msg):
        self.status_label.setText(f'Ошибка: {error_msg}')
        self.log_text.append(f'❌ {error_msg}')
        self.update_timer.stop()

    def _schedule_next_fetch(self):
        return

    def update_plantations_table(self, plantations):
        self.plantations_table.setRowCount(len(plantations))
        for i, plant in enumerate(plantations):
            self.plantations_table.setItem(i, 0, QTableWidgetItem(str(plant['position'][0])))
            self.plantations_table.setItem(i, 1, QTableWidgetItem(str(plant['position'][1])))
            self.plantations_table.setItem(i, 2, QTableWidgetItem(str(plant.get('hp', 0))))
            self.plantations_table.setItem(i, 3, QTableWidgetItem('✓' if plant.get('isMain') else ''))
            self.plantations_table.setItem(i, 4, QTableWidgetItem('⚠' if plant.get('isIsolated') else ''))

    def update_upgrades_table(self, upgrades):
        tiers = upgrades.get('tiers', [])
        points = upgrades.get('points', 0)
        self.upgrades_table.setRowCount(len(tiers) + 1)

        self.upgrades_table.setItem(0, 0, QTableWidgetItem('💰 ДОСТУПНО ОЧКОВ'))
        self.upgrades_table.setItem(0, 1, QTableWidgetItem(str(points)))
        self.upgrades_table.setItem(0, 2, QTableWidgetItem(f"через {upgrades.get('turnsUntilPoints', 0)} ходов"))

        name_map = {
            'repair_power': 'Ремонт/Стройка',
            'max_hp': 'Макс. HP',
            'settlement_limit': 'Лимит плантаций',
            'signal_range': 'Радиус сигнала',
            'vision_range': 'Радиус обзора',
            'decay_mitigation': 'Защита от деградации',
            'earthquake_mitigation': 'Защита от землетрясений',
            'beaver_damage_mitigation': 'Защита от бобров'
        }

        for i, tier in enumerate(tiers):
            name = name_map.get(tier.get('name', ''), tier.get('name', ''))
            self.upgrades_table.setItem(i + 1, 0, QTableWidgetItem(name))
            self.upgrades_table.setItem(i + 1, 1, QTableWidgetItem(f"{tier.get('current', 0)}/{tier.get('max', 0)}"))

    def on_cell_selected(self, x, y):
        if not self.map_widget.game_state:
            return

        state = self.map_widget.game_state
        pos_t = (int(x), int(y))
        base_info = f"📍 ({x}, {y})\n"
        info = base_info
        info += f"Бонусная: {'Да' if (x % 7 == 0 and y % 7 == 0) else 'Нет'}\n"

        mountains = {tuple(m) for m in state.get('mountains', []) if isinstance(m, (list, tuple)) and len(m) == 2}
        if pos_t in mountains:
            info += "Тип: Гора\n"
        else:
            info += "Тип: Пустыня\n"

        for storm in state.get('meteoForecasts', []) or []:
            try:
                if storm.get('kind') == 'sandstorm' and not storm.get('forming', True):
                    sx, sy = storm.get('position', [None, None])
                    if sx is None or sy is None:
                        continue
                    radius = int(storm.get('radius', 1))
                    if abs(int(x) - int(sx)) <= radius and abs(int(y) - int(sy)) <= radius:
                        info += f"Буря: Да (R={radius})\n"
            except Exception:
                pass

        for plant in state.get('plantations', []):
            if self.map_widget._pos_to_tuple(plant.get('position')) == pos_t:
                info += "\nСвой объект: Плантация\n"
                info += f"HP: {plant.get('hp')}\n"
                info += f"ЦУ: {'Да' if plant.get('isMain') else 'Нет'}\n"
                info += f"Изолирована: {'Да' if plant.get('isIsolated') else 'Нет'}\n"

        for enemy in self.map_widget.enemies():
            if enemy.get('position') == pos_t:
                info += "\nВраг: Плантация\n"
                info += f"HP: {enemy.get('hp')}\n"

        for const in state.get('construction', []):
            if self.map_widget._pos_to_tuple(const.get('position')) == pos_t:
                info += "\nСтройка\n"
                info += f"Прогресс: {const.get('progress')}/50\n"

        for beaver in state.get('beavers', []):
            if self.map_widget._pos_to_tuple(beaver.get('position')) == pos_t:
                info += "\nБобры\n"
                info += f"HP: {beaver.get('hp')}\n"

        for cell in state.get('cells', []):
            if self.map_widget._pos_to_tuple(cell.get('position')) == pos_t:
                info += "\nКлетка\n"
                info += f"Терраформация: {cell.get('terraformationProgress')}%\n"
                if 'turnsUntilDegradation' in cell:
                    info += f"До деградации: {cell.get('turnsUntilDegradation')}\n"

        if info == base_info:
            info += "\n(На клетке нет известных объектов)\n"

        self.cell_info.setText(info)

    def zoom_in(self):
        self.map_widget.zoom_level = min(self.map_widget.max_zoom, self.map_widget.zoom_level * 1.2)
        self.map_widget.update()
        self.zoom_label.setText(f'Зум: {self.map_widget.zoom_level:.1f}x')

    def zoom_out(self):
        self.map_widget.zoom_level = max(self.map_widget.min_zoom, self.map_widget.zoom_level / 1.2)
        self.map_widget.update()
        self.zoom_label.setText(f'Зум: {self.map_widget.zoom_level:.1f}x')

    def on_zoom_changed(self, zoom_level: float):
        self.zoom_label.setText(f'Зум: {zoom_level:.1f}x')

    def on_hover_cell(self, x: int, y: int):
        state = self.map_widget.game_state
        if not state or not isinstance(state, dict):
            return
        turn_no = state.get('turnNo', '?')
        self.status_label.setText(f'Ход {turn_no} | Курсор: ({x}, {y})')


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
