# src/spatial/models.py

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal


SpatialLayer = Literal[
"object",
"property",
"action",
"space",
"location",
]


_SLUG_RE = re.compile(r"[^0-9a-zа-яё_]+", re.IGNORECASE)
_MULTI_UNDERSCORE_RE = re.compile(r"_+")


def slugify(text: str) -> str:
    """
    Превращает произвольное имя в канонический технический ID.

    Примеры:
        "Каин" -> "каин"
        "за дверью" -> "за_дверью"
        "Голос из-за двери" -> "голос_из_за_двери"
    """
    text = (text or "").strip().lower()
    text = text.replace(" ", "_")
    text = _SLUG_RE.sub("_", text)
    text = _MULTI_UNDERSCORE_RE.sub("_", text)
    return text.strip("_")


def normalize_slug_list(values: list[str] | tuple[str, ...]) -> list[str]:
    """
    Нормализует список тегов и убирает дубликаты.
    """
    result: set[str] = set()

    for value in values:
        slug = slugify(value)
        if slug:
            result.add(slug)

    return sorted(result)


@dataclass
class EntityDefinition:
    """
    Карточка сущности в онтологическом реестре.

    Это НЕ состояние сущности во времени.
    Это её каноническое описание:
    - имя;
    - алиасы;
    - тип;
    - возможные свойства;
    - возможные действия.
    """

    canonical_id: str
    layer: SpatialLayer
    display_name: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    object_type: str | None = None
    possible_properties: list[str] = field(default_factory=list)
    possible_actions: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.canonical_id = slugify(self.canonical_id)
        self.display_name = (self.display_name or "").strip()
        self.aliases = normalize_slug_list(self.aliases)
        self.possible_properties = normalize_slug_list(self.possible_properties)
        self.possible_actions = normalize_slug_list(self.possible_actions)

        if not self.canonical_id:
            raise ValueError("EntityDefinition.canonical_id cannot be empty")

        if not self.display_name:
            self.display_name = self.canonical_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "layer": self.layer,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "description": self.description,
            "object_type": self.object_type,
            "possible_properties": list(self.possible_properties),
            "possible_actions": list(self.possible_actions),
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntityDefinition:
        return cls(
            canonical_id=str(data.get("canonical_id", "")),
            layer=data.get("layer", "object"),
            display_name=str(data.get("display_name", "")),
            aliases=list(data.get("aliases", [])),
            description=str(data.get("description", "")),
            object_type=data.get("object_type"),
            possible_properties=list(data.get("possible_properties", [])),
            possible_actions=list(data.get("possible_actions", [])),
            extra=dict(data.get("extra", {})),
        )


@dataclass
class Registry:
    """
    Реестр канонических сущностей.

    Используется для:
    - Entity Resolution;
    - передачи списка существующих тегов в 8B;
    - предотвращения синонимического ада.
    """

    entities: dict[str, EntityDefinition] = field(default_factory=dict)

    def upsert(self, entity: EntityDefinition) -> None:
        self.entities[entity.canonical_id] = entity

    def get(self, canonical_id: str) -> EntityDefinition | None:
        return self.entities.get(slugify(canonical_id))

    def resolve_alias(self, alias: str) -> EntityDefinition | None:
        """
        Простой поиск сущности по каноническому имени или алиасу.
        """
        target = slugify(alias)

        if not target:
            return None

        for entity in self.entities.values():
            if target == entity.canonical_id:
                return entity

            if target in entity.aliases:
                return entity

        return None

    def resolve_alias_all(self, alias: str) -> list[EntityDefinition]:
        """
        Возвращает все сущности, у которых есть такой алиас.

        Нужно для случаев, когда один алиас может быть спорным.
        Например, "он" может относиться к разным персонажам.
        """
        target = slugify(alias)

        if not target:
            return []

        result: list[EntityDefinition] = []

        for entity in self.entities.values():
            if target == entity.canonical_id or target in entity.aliases:
                result.append(entity)

        return result

    def by_layer(self, layer: SpatialLayer) -> list[EntityDefinition]:
        return [
            entity
            for entity in self.entities.values()
            if entity.layer == layer
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": [
                entity.to_dict()
                for entity in self.entities.values()
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Registry:
        registry = cls()

        for entity_data in data.get("entities", []):
            entity = EntityDefinition.from_dict(entity_data)
            registry.upsert(entity)

        return registry


@dataclass
class CharacterSheet:
    """
    Character sheet data embedded in Placement.metrics.
    
    Stores RPG game state tied to a spatial entity.
    References: src/game/character.py for full Character class.
    """
    hp: int = 30
    max_hp: int = 30
    mana: int = 50
    max_mana: int = 50
    stamina: int = 50
    max_stamina: int = 50
    
    # D&D 5e stats
    strength: int = 10
    dexterity: int = 10
    constitution: int = 10
    intelligence: int = 10
    wisdom: int = 10
    charisma: int = 10
    
    level: int = 1
    experience: int = 0
    
    inventory: list[str] = field(default_factory=list)
    equipped_slots: dict[str, str] = field(default_factory=dict)
    skills: dict[str, int] = field(default_factory=dict)
    conditions: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "hp": self.hp,
            "max_hp": self.max_hp,
            "mana": self.mana,
            "max_mana": self.max_mana,
            "stamina": self.stamina,
            "max_stamina": self.max_stamina,
            "stats": {
                "STR": self.strength,
                "DEX": self.dexterity,
                "CON": self.constitution,
                "INT": self.intelligence,
                "WIS": self.wisdom,
                "CHA": self.charisma,
            },
            "level": self.level,
            "experience": self.experience,
            "inventory": self.inventory,
            "equipped_slots": self.equipped_slots,
            "skills": self.skills,
            "conditions": self.conditions,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CharacterSheet:
        sheet = cls()
        sheet.hp = data.get("hp", 30)
        sheet.max_hp = data.get("max_hp", 30)
        sheet.mana = data.get("mana", 50)
        sheet.max_mana = data.get("max_mana", 50)
        sheet.stamina = data.get("stamina", 50)
        sheet.max_stamina = data.get("max_stamina", 50)
        sheet.level = data.get("level", 1)
        sheet.experience = data.get("experience", 0)
        
        stats = data.get("stats", {})
        sheet.strength = stats.get("STR", 10)
        sheet.dexterity = stats.get("DEX", 10)
        sheet.constitution = stats.get("CON", 10)
        sheet.intelligence = stats.get("INT", 10)
        sheet.wisdom = stats.get("WIS", 10)
        sheet.charisma = stats.get("CHA", 10)
        
        sheet.inventory = list(data.get("inventory", []))
        sheet.equipped_slots = dict(data.get("equipped_slots", {}))
        sheet.skills = dict(data.get("skills", {}))
        sheet.conditions = list(data.get("conditions", []))
        
        return sheet


@dataclass
class Placement:
    """
    Положение одного объекта внутри кадра.

    Например:
        object_id: лилит
        location_id: перед_дверью
        position: сидит на полу, спиной к двери
        properties: страх, ломка_от_крови
        actions: вжимается_в_дверь
        character_sheet: {hp, stats, level, ...}  ← NEW: Game state
    """

    object_id: str
    location_id: str | None = None
    position: str = ""
    properties: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    character_sheet: CharacterSheet | None = None

    def __post_init__(self) -> None:
        self.object_id = slugify(self.object_id)

        if self.location_id is not None:
            self.location_id = slugify(self.location_id)

        self.properties = normalize_slug_list(self.properties)
        self.actions = normalize_slug_list(self.actions)

        if not self.object_id:
            raise ValueError("Placement.object_id cannot be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "location_id": self.location_id,
            "position": self.position,
            "properties": list(self.properties),
            "actions": list(self.actions),
            "metrics": dict(self.metrics),
            "character_sheet": self.character_sheet.to_dict() if self.character_sheet else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Placement:
        sheet = None
        if data.get("character_sheet"):
            sheet = CharacterSheet.from_dict(data["character_sheet"])
        
        return cls(
            object_id=str(data.get("object_id", "")),
            location_id=data.get("location_id"),
            position=str(data.get("position", "")),
            properties=list(data.get("properties", [])),
            actions=list(data.get("actions", [])),
            metrics=dict(data.get("metrics", {})),
            character_sheet=sheet,
        )


@dataclass
class Frame:
    """
    Один кадр времени.

    Это главный источник истины о состоянии сцены.

    frame_id:
        порядковый номер кадра: 1, 2, 3...
    previous_frame_id:
        предыдущий кадр. Для первого кадра None.
    time_anchor_id:
        ID временного якоря в RHIZ. Может быть None, если кадр
        создается отдельно от долгосрочного rhiz.
    space_id:
        основное пространство кадра.
    placements:
        объекты, их расположение, свойства и действия.
    """

    frame_id: int
    previous_frame_id: int | None
    time_anchor_id: int | None
    space_id: str
    interval: str = ""
    summary: str = ""
    placements: list[Placement] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.space_id = slugify(self.space_id)

        if self.frame_id <= 0:
            raise ValueError("Frame.frame_id must be positive")

        if self.previous_frame_id is not None and self.previous_frame_id <= 0:
            raise ValueError("Frame.previous_frame_id must be positive or None")

    def validate(self, strict_sequence: bool = True) -> list[str]:
        problems: list[str] = []

        if self.frame_id <= 0:
            problems.append(f"Invalid frame_id: {self.frame_id}")

        if strict_sequence:
            if self.frame_id == 1:
                if self.previous_frame_id is not None:
                    problems.append(
                        "First frame must have previous_frame_id=None"
                    )
            else:
                expected_previous = self.frame_id - 1

                if self.previous_frame_id != expected_previous:
                    problems.append(
                        f"Frame {self.frame_id} must have "
                        f"previous_frame_id={expected_previous}, "
                        f"got {self.previous_frame_id}"
                    )

        if not self.space_id:
            problems.append("Frame.space_id cannot be empty")

        for index, placement in enumerate(self.placements):
            if not placement.object_id:
                problems.append(
                    f"Placement[{index}] has empty object_id"
                )

        return problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "previous_frame_id": self.previous_frame_id,
            "time_anchor_id": self.time_anchor_id,
            "space_id": self.space_id,
            "interval": self.interval,
            "summary": self.summary,
            "placements": [
                placement.to_dict()
                for placement in self.placements
            ],
            "events": list(self.events),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Frame:
        placements = [
            Placement.from_dict(item)
            for item in data.get("placements", [])
        ]

        return cls(
            frame_id=int(data.get("frame_id", 0)),
            previous_frame_id=data.get("previous_frame_id"),
            time_anchor_id=data.get("time_anchor_id"),
            space_id=str(data.get("space_id", "")),
            interval=str(data.get("interval", "")),
            summary=str(data.get("summary", "")),
            placements=placements,
            events=list(data.get("events", [])),
            created_at=float(data.get("created_at", time.time())),
        )


@dataclass
class FrameIndexEntry:
    """
    Короткая запись в индексе кадров.

    Нужна, чтобы не читать все кадры подряд при поиске последнего.
    """

    frame_id: int
    previous_frame_id: int | None
    time_anchor_id: int | None
    space_id: str
    summary: str = ""
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "previous_frame_id": self.previous_frame_id,
            "time_anchor_id": self.time_anchor_id,
            "space_id": self.space_id,
            "summary": self.summary,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FrameIndexEntry:
        return cls(
            frame_id=int(data.get("frame_id", 0)),
            previous_frame_id=data.get("previous_frame_id"),
            time_anchor_id=data.get("time_anchor_id"),
            space_id=str(data.get("space_id", "")),
            summary=str(data.get("summary", "")),
            created_at=float(data.get("created_at", 0.0)),
        )


@dataclass
class FrameIndex:
    """
    Индекс всех кадров.

    Гарантирует, что кадры идут строго последовательно:
        1, 2, 3, 4...
    """

    last_frame_id: int = 0
    last_time_anchor_id: int | None = None
    entries: list[FrameIndexEntry] = field(default_factory=list)

    def next_frame_id(self) -> int:
        return self.last_frame_id + 1

    def append_frame(self, frame: Frame) -> None:
        expected_frame_id = self.next_frame_id()

        if frame.frame_id != expected_frame_id:
            raise ValueError(
                f"Expected frame_id={expected_frame_id}, "
                f"got {frame.frame_id}"
            )

        expected_previous = self.last_frame_id if self.last_frame_id > 0 else None

        if frame.previous_frame_id != expected_previous:
            raise ValueError(
                f"Frame {frame.frame_id} must have "
                f"previous_frame_id={expected_previous}, "
                f"got {frame.previous_frame_id}"
            )

        self.entries.append(
            FrameIndexEntry(
                frame_id=frame.frame_id,
                previous_frame_id=frame.previous_frame_id,
                time_anchor_id=frame.time_anchor_id,
                space_id=frame.space_id,
                summary=frame.summary,
                created_at=frame.created_at,
            )
        )

        self.last_frame_id = frame.frame_id

        if frame.time_anchor_id is not None:
            self.last_time_anchor_id = frame.time_anchor_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_frame_id": self.last_frame_id,
            "last_time_anchor_id": self.last_time_anchor_id,
            "entries": [
                entry.to_dict()
                for entry in self.entries
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FrameIndex:
        entries = [
            FrameIndexEntry.from_dict(item)
            for item in data.get("entries", [])
        ]

        return cls(
            last_frame_id=int(data.get("last_frame_id", 0)),
            last_time_anchor_id=data.get("last_time_anchor_id"),
            entries=entries,
        )