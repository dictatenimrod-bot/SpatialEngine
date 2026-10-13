# src/spatial/atomizer.py

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.llm.client import LLMClient, LLMError
from src.llm.json_extractor import extract_json

from .registry import SpatialRegistry


# ============================================================
# МОДЕЛИ РЕЗУЛЬТАТА АТОМИЗАЦИИ
# ============================================================

@dataclass
class AtomizedCharacter:
    canonical_id: str
    display_name: str
    description: str = ""
    properties: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    location_id: str = ""
    is_new: bool = False


@dataclass
class AtomizedObject:
    canonical_id: str
    display_name: str
    object_type: str = "предмет"
    description: str = ""
    properties: list[str] = field(default_factory=list)
    location_id: str = ""
    is_new: bool = False


@dataclass
class AtomizedProperty:
    canonical_id: str
    display_name: str
    description: str = ""
    attached_to: list[str] = field(default_factory=list)
    is_new: bool = False


@dataclass
class AtomizedAction:
    canonical_id: str
    display_name: str
    description: str = ""
    actor_id: str = ""
    is_new: bool = False


@dataclass
class AtomizedSpace:
    canonical_id: str
    display_name: str
    description: str = ""
    is_new: bool = False


@dataclass
class AtomizedLocation:
    canonical_id: str
    display_name: str
    space_id: str = ""
    description: str = ""
    is_new: bool = False


@dataclass
class SceneAnalysis:
    """
    Результат атомизации сцены.

    Содержит все извлечённые сущности, разбитые по слоям.
    """
    characters: list[AtomizedCharacter] = field(default_factory=list)
    objects: list[AtomizedObject] = field(default_factory=list)
    properties: list[AtomizedProperty] = field(default_factory=list)
    actions: list[AtomizedAction] = field(default_factory=list)
    spaces: list[AtomizedSpace] = field(default_factory=list)
    locations: list[AtomizedLocation] = field(default_factory=list)
    time_marker: str = ""
    time_description: str = ""
    raw_response: str = ""

    @property
    def total_entities(self) -> int:
        return (
            len(self.characters)
            + len(self.objects)
            + len(self.properties)
            + len(self.actions)
            + len(self.spaces)
            + len(self.locations)
        )


# ============================================================
# ПРОМПТ АТОМИЗАТОРА
# ============================================================

def _build_atomizer_prompt(
    scene_text: str,
    registry_text: str,
    living_state_text: str,
    player_text: str = "",
) -> str:
    """
    Строит промпт для LLM-атомизатора.

    Включает:
    - реестр существующих сущностей (чтобы не плодить дубликаты);
    - living state (для контекста текущей сцены);
    - саму сцену для разбора.
    """
    player_block = (
        "ИГРОК (собеседник персонажа):\n"
        f"{player_text}\n"
        "Используй его имя вместо «незнакомец» и «кто-то».\n\n"
        if player_text
        else ""
    )
    return (
        "Ты — онтологический процессор физической реальности.\n"
        "Твоя задача — разобрать сцену на атомарные сущности.\n\n"
        "РЕЕСТР СУЩЕСТВУЮЩИХ СУЩНОСТЕЙ:\n"
        "Если сущность уже есть в реестре — ОБЯЗАН использовать её каноническое имя.\n"
        "Запрещено создавать дубликаты для местоимений и синонимов.\n\n"
        f"{registry_text}\n\n"
        "ТЕКУЩЕЕ СОСТОЯНИЕ СЦЕНЫ (Living State):\n"
        f"{living_state_text}\n\n"
        f"{player_block}"
        "НОВАЯ СЦЕНА ДЛЯ РАЗБОРА:\n"
        f"{scene_text}\n\n"
        "ПРАВИЛА РАЗБОРА:\n"
        "1. characters: Персонажи (живые существа с агентностью). "
        "Для каждого укажи: canonical_id, display_name, desc, properties, actions, location_id.\n"
        "2. objects: Предметы, преграды, интерьер. "
        "Для каждого укажи: canonical_id, display_name, object_type, desc, properties, location_id.\n"
        "3. properties: Свойства, состояния, эмоции, физиологические маркеры. "
        "Для каждого укажи: canonical_id, display_name, desc, attached_to.\n"
        "4. actions: Действия, которые совершают персонажи. "
        "Для каждого укажи: canonical_id, display_name, desc, actor_id.\n"
        "5. spaces: Пространства (комнаты, зоны). "
        "Для каждого укажи: canonical_id, display_name, desc.\n"
        "6. locations: Конкретные точки внутри пространств. "
        "Для каждого укажи: canonical_id, display_name, space_id, desc.\n"
        "7. time: Временной маркер и описание дельты времени.\n\n"
        "8. Если в сцене действует собеседник персонажа — используй его имя "
        "из блока [ИГРОК]. Запрещено создавать сущности stranger/незнакомец, "
        "если имя игрока известно.\n\n"
        "ЗАПРЕТЫ:\n"
        "- Не создавай новую сущность, если она уже есть в реестре.\n"
        "- Не используй технические ID вида user_01, ai_01, space_01, object_01. "
        "canonical_id — осмысленное короткое имя из сцены "
        "(лилит, дверь, спальня).\n"
        "- Не используй абстракции ('тьма', 'пустота', 'атмосфера').\n"
        "- Не используй мета-слова ('анализ', 'контекст', 'модель').\n"
        "- Пиши только физический лор от 3-го лица.\n\n"
            "- Если в сцене действует собеседник персонажа — бери его имя из блока "
        "[ИГРОК]. Запрещено создавать stranger/незнакомец, если имя известно.\n"
        "Верни СТРОГО один JSON-объект без markdown:\n"
        "{\n"
        '  "characters": [\n'
        '    {"canonical_id": "...", "display_name": "...", "desc": "...", '
        '"properties": ["..."], "actions": ["..."], "location_id": "..."}\n'
        "  ],\n"
        '  "objects": [\n'
        '    {"canonical_id": "...", "display_name": "...", "object_type": "предмет", '
        '"desc": "...", "properties": ["..."], "location_id": "..."}\n'
        "  ],\n"
        '  "properties": [\n'
        '    {"canonical_id": "...", "display_name": "...", "desc": "...", '
        '"attached_to": ["..."]}\n'
        "  ],\n"
        '  "actions": [\n'
        '    {"canonical_id": "...", "display_name": "...", "desc": "...", '
        '"actor_id": "..."}\n'
        "  ],\n"
        '  "spaces": [\n'
        '    {"canonical_id": "...", "display_name": "...", "desc": "..."}\n'
        "  ],\n"
        '  "locations": [\n'
        '    {"canonical_id": "...", "display_name": "...", "space_id": "...", '
        '"desc": "..."}\n'
        "  ],\n"
        '  "time": {"marker": "...", "desc": "..."}\n'
        "}"
    )


# ============================================================
# ПАРСИНГ ОТВЕТА LLM
# ============================================================

def _parse_scene_analysis(raw: str) -> SceneAnalysis:
    """
    Парсит JSON-ответ LLM в SceneAnalysis.

    Устойчив к отсутствующим полям и неверным типам.
    """
    data = extract_json(raw)

    if not isinstance(data, dict):
        return SceneAnalysis(raw_response=raw)

    analysis = SceneAnalysis(raw_response=raw)

    # Characters
    for item in data.get("characters", []):
        if not isinstance(item, dict):
            continue
        canonical_id = str(item.get("canonical_id", "")).strip()
        if not canonical_id:
            continue
        analysis.characters.append(
            AtomizedCharacter(
                canonical_id=canonical_id,
                display_name=str(item.get("display_name", canonical_id)),
                description=str(item.get("desc", "")),
                properties=[str(p) for p in item.get("properties", []) if p],
                actions=[str(a) for a in item.get("actions", []) if a],
                location_id=str(item.get("location_id", "")),
            )
        )

    # Objects
    for item in data.get("objects", []):
        if not isinstance(item, dict):
            continue
        canonical_id = str(item.get("canonical_id", "")).strip()
        if not canonical_id:
            continue
        analysis.objects.append(
            AtomizedObject(
                canonical_id=canonical_id,
                display_name=str(item.get("display_name", canonical_id)),
                object_type=str(item.get("object_type", "предмет")),
                description=str(item.get("desc", "")),
                properties=[str(p) for p in item.get("properties", []) if p],
                location_id=str(item.get("location_id", "")),
            )
        )

    # Properties
    for item in data.get("properties", []):
        if not isinstance(item, dict):
            continue
        canonical_id = str(item.get("canonical_id", "")).strip()
        if not canonical_id:
            continue
        analysis.properties.append(
            AtomizedProperty(
                canonical_id=canonical_id,
                display_name=str(item.get("display_name", canonical_id)),
                description=str(item.get("desc", "")),
                attached_to=[str(a) for a in item.get("attached_to", []) if a],
            )
        )

    # Actions
    for item in data.get("actions", []):
        if not isinstance(item, dict):
            continue
        canonical_id = str(item.get("canonical_id", "")).strip()
        if not canonical_id:
            continue
        analysis.actions.append(
            AtomizedAction(
                canonical_id=canonical_id,
                display_name=str(item.get("display_name", canonical_id)),
                description=str(item.get("desc", "")),
                actor_id=str(item.get("actor_id", "")),
            )
        )

    # Spaces
    for item in data.get("spaces", []):
        if not isinstance(item, dict):
            continue
        canonical_id = str(item.get("canonical_id", "")).strip()
        if not canonical_id:
            continue
        analysis.spaces.append(
            AtomizedSpace(
                canonical_id=canonical_id,
                display_name=str(item.get("display_name", canonical_id)),
                description=str(item.get("desc", "")),
            )
        )

    # Locations
    for item in data.get("locations", []):
        if not isinstance(item, dict):
            continue
        canonical_id = str(item.get("canonical_id", "")).strip()
        if not canonical_id:
            continue
        analysis.locations.append(
            AtomizedLocation(
                canonical_id=canonical_id,
                display_name=str(item.get("display_name", canonical_id)),
                space_id=str(item.get("space_id", "")),
                description=str(item.get("desc", "")),
            )
        )

    # Time
    time_data = data.get("time", {})
    if isinstance(time_data, dict):
        analysis.time_marker = str(time_data.get("marker", ""))
        analysis.time_description = str(time_data.get("desc", ""))

    return analysis


# ============================================================
# АТОМИЗАТОР
# ============================================================

class SpatialAtomizer:
    """
    LLM-атомизатор сцен.

    Принимает текст сцены, реестр сущностей и living state.
    Отправляет в LLM промпт с реестром (чтобы модель не плодила дубликаты).
    Возвращает структурированный SceneAnalysis.

    Этот класс НЕ:
    - не пишет кадры;
    - не обновляет реестр;
    - не вызывает wave propagation.
    """

    def __init__(
        self,
        llm: LLMClient,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> None:
        self._llm = llm
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def atomize(
        self,
        scene_text: str,
        registry: SpatialRegistry,
        living_state_text: str = "",
        player_text: str = "",
    ) -> SceneAnalysis:
        """
        Атомизирует сцену через LLM.

        Args:
            scene_text: Текст сцены для разбора.
            registry: Реестр сущностей (для предотвращения дубликатов).
            living_state_text: Текущее состояние living state.

        Returns:
            SceneAnalysis с извлечёнными сущностями.
        """
        scene_clean = " ".join(str(scene_text or "").split())

        if not scene_clean:
            return SceneAnalysis()

        registry_text = registry.entities_for_prompt()

        prompt = _build_atomizer_prompt(
            scene_text=scene_clean,
            registry_text=registry_text,
            living_state_text=living_state_text,
            player_text=player_text,
        )

        player_block = (
            "\n[ИГРОК (собеседник персонажа)]:\n"
            f"{player_text}\n\n"
            if player_text
            else ""
        )

        messages = [
            {
                "role": "user",
                "content": prompt,
            }
        ]

        try:
            raw_response = await self._llm.chat(
                model=self._model,
                messages=messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                reasoning_budget = 500,
            )
        except LLMError as exc:
            print(f"[SPATIAL ATOMIZER] LLM error: {exc}")
            return SceneAnalysis()
        # Логируем сырой ответ (нужен model_dir, но у нас его нет — пишем в общий лог)
        print(f"[SPATIAL ATOMIZER] Response ({len(raw_response)} chars):")
        print(raw_response, flush=True)
        if not raw_response:
            return SceneAnalysis()

        analysis = _parse_scene_analysis(raw_response)

        # Помечаем новые сущности
        self._mark_new_entities(analysis, registry)

        return analysis

    def _mark_new_entities(
        self,
        analysis: SceneAnalysis,
        registry: SpatialRegistry,
    ) -> None:
        """
        Помечает сущности как новые, если их нет в реестре.
        """
        for character in analysis.characters:
            existing = registry.find_by_name(character.canonical_id, layer="character")
            character.is_new = existing is None

        for obj in analysis.objects:
            existing = registry.find_by_name(obj.canonical_id, layer="object")
            obj.is_new = existing is None

        for prop in analysis.properties:
            existing = registry.find_by_name(prop.canonical_id, layer="property")
            prop.is_new = existing is None

        for action in analysis.actions:
            existing = registry.find_by_name(action.canonical_id, layer="action")
            action.is_new = existing is None

        for space in analysis.spaces:
            existing = registry.find_by_name(space.canonical_id, layer="space")
            space.is_new = existing is None

        for location in analysis.locations:
            existing = registry.find_by_name(location.canonical_id, layer="location")
            location.is_new = existing is None