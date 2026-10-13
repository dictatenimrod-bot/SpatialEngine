# src/spatial/registry.py

from __future__ import annotations

from typing import Any

from .models import EntityDefinition, Registry, slugify
from .store import SpatialStore


def _slug_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    """
    Нормализует список строк в список slug-токенов.
    """
    if not values:
        return []

    result: set[str] = set()

    for value in values:
        slug = slugify(value)
        if slug:
            result.add(slug)

    return sorted(result)


class SpatialRegistry:
    """
    Сервис реестра пространственных сущностей.

    Отвечает за:
    - поиск сущности по каноническому имени;
    - поиск сущности по алиасу;
    - добавление новых сущностей;
    - слияние алиасов, если сущность уже существует;
    - подготовку списка сущностей для LLM-атомизатора.

    Этот класс НЕ:
    - не вызывает LLM;
    - не пишет кадры;
    - не принимает решений о сцене.
    """

    def __init__(self, store: SpatialStore) -> None:
        self._store = store
        self._registry: Registry = store.load_registry()

    # ==========================================================
    # PROPERTIES
    # ==========================================================

    @property
    def registry(self) -> Registry:
        return self._registry

    @property
    def entities_count(self) -> int:
        return len(self._registry.entities)

    # ==========================================================
    # LOAD / SAVE
    # ==========================================================

    def reload(self) -> None:
        """
        Перечитывает registry.json с диска.
        """
        self._registry = self._store.load_registry()

    def save(self) -> None:
        """
        Атомарно сохраняет registry.json на диск.
        """
        self._store.save_registry(self._registry)

    # ==========================================================
    # LOOKUP
    # ==========================================================

    def get(self, canonical_id: str) -> EntityDefinition | None:
        """
        Возвращает сущность по каноническому ID.
        """
        return self._registry.get(canonical_id)

    def find_by_name(
        self,
        name: str,
        layer: str | None = None,
    ) -> EntityDefinition | None:
        """
        Ищет сущность по каноническому имени или алиасу.

        Если layer передан, фильтрует по слою:
            character / object / property / action / space / location
        """
        slug = slugify(name)

        if not slug:
            return None

        # 1. Прямое совпадение canonical_id.
        entity = self._registry.get(slug)

        if entity is not None:
            if layer is None or entity.layer == layer:
                return entity

        # 2. Поиск по алиасам.
        candidates = self.find_all_by_name(name, layer=layer)

        if not candidates:
            return None

        # Приоритет:
        # 1) точное совпадение canonical_id;
        # 2) совпадение layer;
        # 3) более короткий canonical_id.
        candidates.sort(
            key=lambda e: (
                e.canonical_id != slug,
                bool(layer) and e.layer != layer,
                len(e.canonical_id),
            )
        )

        return candidates[0]

    def find_all_by_name(
        self,
        name: str,
        layer: str | None = None,
    ) -> list[EntityDefinition]:
        """
        Возвращает все сущности, у которых имя совпадает
        с canonical_id или одним из алиасов.
        """
        slug = slugify(name)

        if not slug:
            return []

        candidates: list[EntityDefinition] = []

        for entity in self._registry.entities.values():
            if slug == entity.canonical_id or slug in entity.aliases:
                if layer is None or entity.layer == layer:
                    candidates.append(entity)

        return candidates

    def by_layer(self, layer: str) -> list[EntityDefinition]:
        """
        Возвращает все сущности указанного слоя.
        """
        return [
            entity
            for entity in self._registry.entities.values()
            if entity.layer == layer
        ]

    # ==========================================================
    # UPSERT
    # ==========================================================

    def upsert_entity(
        self,
        display_name: str,
        layer: str,
        canonical_id: str | None = None,
        aliases: list[str] | None = None,
        description: str = "",
        object_type: str | None = None,
        possible_properties: list[str] | None = None,
        possible_actions: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> EntityDefinition:
        """
        Создаёт новую сущность или обновляет существующую.

        Если сущность уже найдена по canonical_id или алиасу,
        новые алиасы и свойства объединяются с существующими.
        """
        display_name_clean = " ".join(str(display_name or "").split())

        canonical_slug = slugify(canonical_id or display_name_clean)

        if not canonical_slug:
            raise ValueError("SpatialRegistry.upsert_entity: empty canonical_id")

        existing = self.find_by_name(canonical_slug, layer=layer)

        if existing is None and display_name_clean:
            existing = self.find_by_name(display_name_clean, layer=layer)

        if existing is not None:
            merged_aliases: set[str] = set(existing.aliases)

            merged_aliases.update(_slug_list(aliases))

            display_slug = slugify(display_name_clean)
            if display_slug and display_slug != existing.canonical_id:
                merged_aliases.add(display_slug)

            if canonical_slug != existing.canonical_id:
                merged_aliases.add(canonical_slug)

            merged_aliases.discard(existing.canonical_id)

            merged_properties = sorted(
                set(existing.possible_properties)
                | set(_slug_list(possible_properties))
            )

            merged_actions = sorted(
                set(existing.possible_actions)
                | set(_slug_list(possible_actions))
            )

            merged_extra = dict(existing.extra)
            if extra:
                merged_extra.update(extra)

            updated = EntityDefinition(
                canonical_id=existing.canonical_id,
                layer=existing.layer,
                display_name=existing.display_name,
                aliases=sorted(merged_aliases),
                description=description.strip() or existing.description,
                object_type=object_type or existing.object_type,
                possible_properties=merged_properties,
                possible_actions=merged_actions,
                extra=merged_extra,
            )

            self._registry.upsert(updated)
            return updated

        entity = EntityDefinition(
            canonical_id=canonical_slug,
            layer=layer,
            display_name=display_name_clean or canonical_slug,
            aliases=_slug_list(aliases),
            description=" ".join(str(description or "").split()),
            object_type=object_type,
            possible_properties=_slug_list(possible_properties),
            possible_actions=_slug_list(possible_actions),
            extra=dict(extra or {}),
        )

        self._registry.upsert(entity)
        return entity

    def add_alias(self, canonical_id: str, alias: str) -> bool:
        """
        Добавляет алиас существующей сущности.

        Возвращает True, если сущность найдена и алиас добавлен.
        """
        entity = self._registry.get(canonical_id)

        if entity is None:
            return False

        alias_slug = slugify(alias)

        if not alias_slug:
            return False

        if alias_slug == entity.canonical_id:
            return True

        if alias_slug in entity.aliases:
            return True

        merged_aliases = set(entity.aliases)
        merged_aliases.add(alias_slug)

        updated = EntityDefinition(
            canonical_id=entity.canonical_id,
            layer=entity.layer,
            display_name=entity.display_name,
            aliases=sorted(merged_aliases),
            description=entity.description,
            object_type=entity.object_type,
            possible_properties=list(entity.possible_properties),
            possible_actions=list(entity.possible_actions),
            extra=dict(entity.extra),
        )

        self._registry.upsert(updated)
        return True

    # ==========================================================
    # LLM PROMPT HELPERS
    # ==========================================================

    def entities_for_prompt(self, max_aliases_per_entity: int = 6) -> str:
        """
        Возвращает человекочитаемый список сущностей для LLM.

        Этот текст нужно вставлять в промпт атомизатора,
        чтобы модель не плодила дубликаты.
        """
        sections: list[tuple[str, str]] = [
            ("character", "ПЕРСОНАЖИ"),
            ("object", "ОБЪЕКТЫ"),
            ("property", "СВОЙСТВА"),
            ("action", "ДЕЙСТВИЯ"),
            ("space", "ПРОСТРАНСТВА"),
            ("location", "ЛОКАЦИИ"),
        ]

        lines: list[str] = []

        for layer, title in sections:
            entities = self.by_layer(layer)

            if not entities:
                continue

            lines.append(f"{title}:")

            for entity in sorted(entities, key=lambda e: e.canonical_id):
                aliases = entity.aliases[:max_aliases_per_entity]

                if aliases:
                    aliases_text = ", ".join(aliases)
                else:
                    aliases_text = "нет"

                lines.append(
                    f"- {entity.canonical_id} | "
                    f"{entity.display_name} | "
                    f"алиасы: {aliases_text}"
                )

            lines.append("")

        if not lines:
            return "Реестр сущностей пуст."

        return "\n".join(lines).strip()

    # ==========================================================
    # HEALTHCHECK
    # ==========================================================

    def healthcheck(self) -> dict[str, Any]:
        by_layer: dict[str, int] = {}

        for entity in self._registry.entities.values():
            by_layer[entity.layer] = by_layer.get(entity.layer, 0) + 1

        return {
            "ok": True,
            "entities_count": len(self._registry.entities),
            "by_layer": by_layer,
        }