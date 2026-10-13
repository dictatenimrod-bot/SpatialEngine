# src/spatial/store.py

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any

from .models import (
    EntityDefinition,
    Frame,
    FrameIndex,
    FrameIndexEntry,
    Placement,
    Registry,
)


# ============================================================
# АТОМАРНАЯ ЗАПИСЬ JSON
# ============================================================

def _atomic_write_json(path: str, payload: Any) -> None:
    """
    Атомарно сохраняет JSON.

    Сначала пишет во временный файл в той же директории,
    затем делает os.replace. Это защищает от повреждения
    при падении процесса посреди записи.
    """
    dirname = os.path.dirname(os.path.abspath(path))
    os.makedirs(dirname, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=dirname, suffix=".tmp")

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        os.replace(tmp_path, path)

    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


# ============================================================
# ПУТИ
# ============================================================

class SpatialPaths:
    """
    Определяет файловую структуру spatial-хранилища.

    Структура:
        base_dir/
          registry.json
          frames/
            index.json
            000001.json
            000002.json
            ...
          entities/
            characters/
            objects/
            properties/
            actions/
            spaces/
            locations/
    """

    def __init__(self, base_dir: str) -> None:
        self.base_dir = os.path.abspath(base_dir)

    @property
    def registry_path(self) -> str:
        return os.path.join(self.base_dir, "registry.json")

    @property
    def frames_dir(self) -> str:
        return os.path.join(self.base_dir, "frames")

    @property
    def frame_index_path(self) -> str:
        return os.path.join(self.frames_dir, "index.json")

    @property
    def entities_dir(self) -> str:
        return os.path.join(self.base_dir, "entities")

    def frame_path(self, frame_id: int) -> str:
        """
        Путь к файлу кадра.

        Кадры именуются с zero-padding для естественной
        сортировки по имени файла:
            000001.json
            000002.json
            ...
            000023.json
        """
        return os.path.join(self.frames_dir, f"{frame_id:06d}.json")

    def entity_path(self, layer: str, canonical_id: str) -> str:
        """
        Путь к файлу определения сущности.

        Пример:
            entities/characters/char.json
            entities/objects/дверь.json
        """
        layer_dir_name = {
            "character": "characters",
            "object": "objects",
            "property": "properties",
            "action": "actions",
            "space": "spaces",
            "location": "locations",
        }.get(layer, layer)

        return os.path.join(
            self.entities_dir,
            layer_dir_name,
            f"{canonical_id}.json",
        )


# ============================================================
# SPATIAL STORE
# ============================================================

class SpatialStore:
    """
    Хранилище пространственных кадров и реестра сущностей.

    Ответственности:
    - атомарная запись кадров на диск;
    - атомарная запись индекса кадров;
    - загрузка кадров по frame_id;
    - загрузка последнего кадра;
    - загрузка и сохранение registry;
    - проверка последовательности кадров.

    Этот класс НЕ:
    - не вызывает LLM;
    - не принимает решений о том, какие сущности создавать;
    - не знает про pipeline или сервер.
    """

    def __init__(self, paths: SpatialPaths) -> None:
        self._paths = paths

        # Создаём базовую структуру директорий
        os.makedirs(self._paths.base_dir, exist_ok=True)
        os.makedirs(self._paths.frames_dir, exist_ok=True)
        os.makedirs(self._paths.entities_dir, exist_ok=True)

    # ==========================================================
    # REGISTRY
    # ==========================================================

    def load_registry(self) -> Registry:
        """
        Загружает реестр сущностей с диска.

        Если файла нет или он повреждён — возвращает пустой реестр.
        """
        registry_path = self._paths.registry_path

        if not os.path.exists(registry_path):
            return Registry()

        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            return Registry.from_dict(data)

        except Exception as exc:
            print(f"[SPATIAL STORE] Ошибка загрузки registry: {exc}")
            return Registry()

    def save_registry(self, registry: Registry) -> None:
        """
        Атомарно сохраняет реестр сущностей на диск.
        """
        _atomic_write_json(
            self._paths.registry_path,
            registry.to_dict(),
        )

    def save_entity(self, entity: EntityDefinition) -> None:
        """
        Сохраняет определение одной сущности в отдельный файл.

        Это дублирует данные из registry.json, но даёт
        быстрый доступ к отдельной сущности без загрузки
        всего реестра.
        """
        entity_path = self._paths.entity_path(
            entity.layer,
            entity.canonical_id,
        )

        _atomic_write_json(entity_path, entity.to_dict())

    def load_entity(self, layer: str, canonical_id: str) -> EntityDefinition | None:
        """
        Загружает определение одной сущности по слою и canonical_id.
        """
        entity_path = self._paths.entity_path(layer, canonical_id)

        if not os.path.exists(entity_path):
            return None

        try:
            with open(entity_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            return EntityDefinition.from_dict(data)

        except Exception:
            return None

    # ==========================================================
    # FRAME INDEX
    # ==========================================================

    def load_frame_index(self) -> FrameIndex:
        """
        Загружает индекс кадров с диска.

        Если файла нет — возвращает пустой индекс.
        """
        index_path = self._paths.frame_index_path

        if not os.path.exists(index_path):
            return FrameIndex()

        try:
            with open(index_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            return FrameIndex.from_dict(data)

        except Exception as exc:
            print(f"[SPATIAL STORE] Ошибка загрузки frame index: {exc}")
            return FrameIndex()

    def save_frame_index(self, index: FrameIndex) -> None:
        """
        Атомарно сохраняет индекс кадров на диск.
        """
        _atomic_write_json(
            self._paths.frame_index_path,
            index.to_dict(),
        )

    # ==========================================================
    # FRAMES
    # ==========================================================

    def append_frame(self, frame: Frame) -> None:
        """
        Добавляет новый кадр в хранилище.

        Проверяет последовательность:
        - frame_id должен быть last_frame_id + 1
        - previous_frame_id должен совпадать с last_frame_id

        Если проверка не проходит — выбрасывает ValueError.

        После успешной записи кадра обновляет индекс.
        """
        # Валидация самого кадра
        problems = frame.validate(strict_sequence=True)

        # Загружаем текущий индекс для проверки последовательности
        index = self.load_frame_index()

        expected_frame_id = index.next_frame_id()

        if frame.frame_id != expected_frame_id:
            raise ValueError(
                f"Frame ID mismatch: expected {expected_frame_id}, "
                f"got {frame.frame_id}"
            )

        expected_previous = index.last_frame_id if index.last_frame_id > 0 else None

        if frame.previous_frame_id != expected_previous:
            raise ValueError(
                f"Previous frame mismatch: expected {expected_previous}, "
                f"got {frame.previous_frame_id}"
            )

        # Записываем файл кадра
        frame_path = self._paths.frame_path(frame.frame_id)
        _atomic_write_json(frame_path, frame.to_dict())

        # Обновляем индекс
        index.append_frame(frame)
        self.save_frame_index(index)

    def load_frame(self, frame_id: int) -> Frame | None:
        """
        Загружает кадр по frame_id.

        Возвращает None, если файл не существует или повреждён.
        """
        frame_path = self._paths.frame_path(frame_id)

        if not os.path.exists(frame_path):
            return None

        try:
            with open(frame_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            return Frame.from_dict(data)

        except Exception as exc:
            print(f"[SPATIAL STORE] Ошибка загрузки кадра {frame_id}: {exc}")
            return None

    def load_latest_frame(self) -> Frame | None:
        """
        Загружает самый последний кадр.

        Использует индекс для определения last_frame_id,
        затем загружает соответствующий файл.
        """
        index = self.load_frame_index()

        if index.last_frame_id <= 0:
            return None

        return self.load_frame(index.last_frame_id)

    def load_frames_range(
        self,
        start_frame_id: int,
        end_frame_id: int,
    ) -> list[Frame]:
        """
        Загружает диапазон кадров [start_frame_id, end_frame_id].

        Пропускает повреждённые или отсутствующие файлы.
        """
        frames: list[Frame] = []

        for frame_id in range(start_frame_id, end_frame_id + 1):
            frame = self.load_frame(frame_id)

            if frame is not None:
                frames.append(frame)

        return frames

    # ==========================================================
    # HEALTHCHECK
    # ==========================================================

    def healthcheck(self) -> dict[str, Any]:
        """
        Проверяет целостность spatial-хранилища.

        Возвращает отчёт с найденными проблемами.
        """
        problems: list[str] = []

        # Проверяем registry
        registry = self.load_registry()
        entity_count = len(registry.entities)

        # Проверяем индекс
        index = self.load_frame_index()

        # Проверяем, что все файлы кадров на месте
        missing_frames: list[int] = []
        corrupted_frames: list[int] = []

        for entry in index.entries:
            frame_path = self._paths.frame_path(entry.frame_id)

            if not os.path.exists(frame_path):
                missing_frames.append(entry.frame_id)
            else:
                frame = self.load_frame(entry.frame_id)
                if frame is None:
                    corrupted_frames.append(entry.frame_id)

        if missing_frames:
            problems.append(
                f"Missing frame files: {missing_frames}"
            )

        if corrupted_frames:
            problems.append(
                f"Corrupted frame files: {corrupted_frames}"
            )

        # Проверяем последовательность индекса
        for i in range(1, len(index.entries)):
            prev_entry = index.entries[i - 1]
            curr_entry = index.entries[i]

            if curr_entry.frame_id != prev_entry.frame_id + 1:
                problems.append(
                    f"Frame sequence gap: {prev_entry.frame_id} -> "
                    f"{curr_entry.frame_id}"
                )

        return {
            "ok": len(problems) == 0,
            "problems": problems,
            "entities_count": entity_count,
            "frames_count": len(index.entries),
            "last_frame_id": index.last_frame_id,
            "last_time_anchor_id": index.last_time_anchor_id,
        }
