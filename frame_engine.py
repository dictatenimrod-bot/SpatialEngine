# src/spatial/frame_engine.py

from __future__ import annotations

import time
from typing import Any

from .atomizer import SceneAnalysis
from .models import EntityDefinition, Frame, Placement, slugify
from .registry import SpatialRegistry
from .store import SpatialStore


class FrameEngine:
    """
    Движок сборки пространственных кадров.

    Принимает SceneAnalysis от атомизатора и time_anchor_id из RHIZ.
    Собирает новый Frame, привязывает к предыдущему кадру,
    записывает в SpatialStore и обновляет SpatialRegistry.

    Этот класс НЕ:
    - не вызывает LLM;
    - не принимает решений о запуске pipeline;
    - не знает про FastAPI.
    """

    def __init__(
        self,
        store: SpatialStore,
        registry: SpatialRegistry,
    ) -> None:
        self._store = store
        self._registry = registry

    # ==========================================================
    # PUBLIC API
    # ==========================================================

    def build_and_save_frame(
        self,
        analysis: SceneAnalysis,
        time_anchor_id: int,
        interval: str = "",
        summary: str = "",
    ) -> Frame:
        """
        Собирает новый кадр из SceneAnalysis и сохраняет его.

        Args:
            analysis: Результат атомизации сцены.
            time_anchor_id: ID временного якоря из RHIZ.
            interval: Описание временной дельты.
            summary: Краткое описание кадра.

        Returns:
            Созданный Frame.

        Raises:
            ValueError: Если нарушена последовательность кадров.
        """
        # 1. Определяем ID кадра и предыдущий кадр
        index = self._store.load_frame_index()
        frame_id = index.next_frame_id()
        previous_frame_id = index.last_frame_id if index.last_frame_id > 0 else None

        # 2. Определяем основное пространство кадра
        space_id = self._resolve_primary_space(analysis)

        # 3. Собираем placements (объекты в локациях)
        placements = self._build_placements(analysis)

        # 4. Собираем события (действия)
        events = self._build_events(analysis)

        # 5. Если summary не передан, генерируем из placements
        if not summary:
            summary = self._generate_summary(placements, events)

        # 6. Создаём кадр
        frame = Frame(
            frame_id=frame_id,
            previous_frame_id=previous_frame_id,
            time_anchor_id=time_anchor_id,
            space_id=space_id,
            interval=interval or analysis.time_description,
            summary=summary,
            placements=placements,
            events=events,
            created_at=time.time(),
        )

        # 7. Валидация последовательности
        problems = frame.validate(strict_sequence=True)
        if problems:
            raise ValueError(
                f"Frame validation failed: {'; '.join(problems)}"
            )

        # 8. Записываем в хранилище
        self._store.append_frame(frame)

        # 9. Обновляем реестр новыми сущностями
        self._update_registry(analysis)

        # 10. Сохраняем реестр
        self._registry.save()

        return frame

    def get_current_frame(self) -> Frame | None:
        """
        Возвращает последний кадр.
        """
        return self._store.load_latest_frame()

    def get_frame(self, frame_id: int) -> Frame | None:
        """
        Возвращает кадр по ID.
        """
        return self._store.load_frame(frame_id)

    def format_frame_for_prompt(self, frame: Frame) -> str:
        """
        Форматирует кадр в человекочитаемый текст для вставки в промпт.
        """
        lines: list[str] = []

        lines.append(f"КАДР {frame.frame_id} | Пространство: {frame.space_id}")

        if frame.interval:
            lines.append(f"Время: {frame.interval}")

        if frame.summary:
            lines.append(f"Сводка: {frame.summary}")

        lines.append("")

        # Placements
        if frame.placements:
            lines.append("ОБЪЕКТЫ В СЦЕНЕ:")
            for placement in frame.placements:
                obj_line = f"  • {placement.object_id}"

                if placement.location_id:
                    obj_line += f" @ {placement.location_id}"

                if placement.position:
                    obj_line += f" — {placement.position}"

                lines.append(obj_line)

                if placement.properties:
                    props = ", ".join(placement.properties)
                    lines.append(f"    свойства: {props}")

                if placement.actions:
                    actions = ", ".join(placement.actions)
                    lines.append(f"    действия: {actions}")

        # Events
        if frame.events:
            lines.append("")
            lines.append("СОБЫТИЯ:")
            for event in frame.events:
                lines.append(f"  → {event}")

        return "\n".join(lines)

    # ==========================================================
    # INTERNAL
    # ==========================================================

    def _resolve_primary_space(self, analysis: SceneAnalysis) -> str:
        """
        Определяет основное пространство кадра.

        Приоритет:
        1. Первое пространство из analysis.spaces
        2. space_id из первой локации
        3. Fallback: "unknown"
        """
        if analysis.spaces:
            return slugify(analysis.spaces[0].canonical_id)

        if analysis.locations:
            space_id = analysis.locations[0].space_id
            if space_id:
                return slugify(space_id)

        return "unknown"

    def _build_placements(self, analysis: SceneAnalysis) -> list[Placement]:
        """
        Собирает список Placement из персонажей и объектов.
        """
        placements: list[Placement] = []

        # Персонажи
        for character in analysis.characters:
            placement = Placement(
                object_id=slugify(character.canonical_id),
                location_id=slugify(character.location_id) if character.location_id else None,
                position=character.description,
                properties=[slugify(p) for p in character.properties if p],
                actions=[slugify(a) for a in character.actions if a],
            )
            placements.append(placement)

        # Объекты
        for obj in analysis.objects:
            placement = Placement(
                object_id=slugify(obj.canonical_id),
                location_id=slugify(obj.location_id) if obj.location_id else None,
                position=obj.description,
                properties=[slugify(p) for p in obj.properties if p],
                actions=[],
            )
            placements.append(placement)

        return placements

    def _build_events(self, analysis: SceneAnalysis) -> list[str]:
        """
        Собирает список событий из действий.
        """
        events: list[str] = []

        for action in analysis.actions:
            actor = slugify(action.actor_id) if action.actor_id else "?"
            action_id = slugify(action.canonical_id)
            events.append(f"{actor}: {action_id}")

        return events

    def _generate_summary(
        self,
        placements: list[Placement],
        events: list[str],
    ) -> str:
        """
        Генерирует краткую сводку кадра.
        """
        parts: list[str] = []

        if placements:
            object_names = [p.object_id for p in placements[:5]]
            parts.append(f"Объекты: {', '.join(object_names)}")

        if events:
            parts.append(f"Событий: {len(events)}")

        return ". ".join(parts) if parts else "Пустой кадр"

    def _update_registry(self, analysis: SceneAnalysis) -> None:
        """
        Обновляет реестр сущностями из SceneAnalysis.
        Дополнительно сохраняет каждую сущность отдельным файлом
        в entities/, чтобы папка не оставалась пустой.
        """
        updated: list[EntityDefinition] = []

        # Персонажи
        for character in analysis.characters:
            updated.append(self._registry.upsert_entity(
                display_name=character.display_name,
                layer="character",
                canonical_id=character.canonical_id,
                description=character.description,
                possible_properties=character.properties,
                possible_actions=character.actions,
            ))

        # Объекты
        for obj in analysis.objects:
            updated.append(self._registry.upsert_entity(
                display_name=obj.display_name,
                layer="object",
                canonical_id=obj.canonical_id,
                description=obj.description,
                object_type=obj.object_type,
                possible_properties=obj.properties,
            ))

        # Свойства
        for prop in analysis.properties:
            updated.append(self._registry.upsert_entity(
                display_name=prop.display_name,
                layer="property",
                canonical_id=prop.canonical_id,
                description=prop.description,
            ))

        # Действия
        for action in analysis.actions:
            updated.append(self._registry.upsert_entity(
                display_name=action.display_name,
                layer="action",
                canonical_id=action.canonical_id,
                description=action.description,
            ))

        # Пространства
        for space in analysis.spaces:
            updated.append(self._registry.upsert_entity(
                display_name=space.display_name,
                layer="space",
                canonical_id=space.canonical_id,
                description=space.description,
            ))

        # Локации
        for location in analysis.locations:
            updated.append(self._registry.upsert_entity(
                display_name=location.display_name,
                layer="location",
                canonical_id=location.canonical_id,
                description=location.description,
            ))

        # Сохраняем каждую сущность отдельным файлом в entities/
        for entity in updated:
            self._store.save_entity(entity)