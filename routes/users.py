"""User gear helpers: match community recipes to the active machine/brewer and grinder."""

from __future__ import annotations

from typing import Any

MATCH_EXACT = "exact"
MATCH_MACHINE = "machine"
MATCH_GRINDER = "grinder"
_MATCH_RANK = {MATCH_EXACT: 0, MATCH_MACHINE: 1, MATCH_GRINDER: 2}

_MACHINE_KINDS = {
    "espresso_machine",
    "machine",
    "espresso",
    "espresso-machine",
    "brewer",
    "brew",
    "filter",
}
_GRINDER_KINDS = {"grinder", "mill"}


def fold_gear(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def names_match(left: str, right: str) -> bool:
    a, b = fold_gear(left), fold_gear(right)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 6 and shorter in longer


def _push_name(names: list[str], seen: set[str], raw: Any) -> None:
    text = str(raw or "").strip()
    key = fold_gear(text)
    if not text or not key or key in seen:
        return
    seen.add(key)
    names.append(text)


def _spec_kind(item: dict[str, Any]) -> str:
    return str(item.get("kind") or item.get("type") or item.get("gear_type") or "").strip().lower().replace(" ", "_")


def _spec_name(item: dict[str, Any]) -> str:
    return str(item.get("model_name") or item.get("name") or "").strip()


def active_brewers(user: dict[str, Any] | None) -> list[str]:
    """Active espresso machines and brewers on the user's setup."""
    data = user or {}
    names: list[str] = []
    seen: set[str] = set()
    _push_name(names, seen, data.get("espresso_machine"))
    for item in data.get("gear_specs") or []:
        if not isinstance(item, dict):
            continue
        if _spec_kind(item) in _MACHINE_KINDS:
            _push_name(names, seen, _spec_name(item))
    return names


def active_grinders(user: dict[str, Any] | None) -> list[str]:
    """Active grinders on the user's setup."""
    data = user or {}
    names: list[str] = []
    seen: set[str] = set()
    _push_name(names, seen, data.get("grinder"))
    for item in data.get("gear_specs") or []:
        if not isinstance(item, dict):
            continue
        if _spec_kind(item) in _GRINDER_KINDS:
            _push_name(names, seen, _spec_name(item))
    return names


def _matches_any(name: str, candidates: list[str]) -> bool:
    return any(names_match(name, item) for item in candidates)


def recipe_match_tier(recipe: dict[str, Any], brewers: list[str], grinders: list[str]) -> str:
    machine_hit = bool(brewers) and _matches_any(str(recipe.get("espresso_machine") or ""), brewers)
    grinder_hit = bool(grinders) and _matches_any(str(recipe.get("grinder") or ""), grinders)
    if machine_hit and grinder_hit:
        return MATCH_EXACT
    if machine_hit:
        return MATCH_MACHINE
    if grinder_hit:
        return MATCH_GRINDER
    return ""


def annotate_community_recipes(
    recipes: list[dict[str, Any]] | None,
    user: dict[str, Any] | None,
) -> dict[str, Any]:
    """Tag recipes with gear_match and surface whether any match the user's setup."""
    brewers = active_brewers(user)
    grinders = active_grinders(user)
    annotated: list[dict[str, Any]] = []
    for row in recipes or []:
        item = dict(row)
        item["gear_match"] = recipe_match_tier(item, brewers, grinders)
        annotated.append(item)
    hits = [row for row in annotated if row.get("gear_match")]
    if not hits:
        return {"recipes": annotated, "fallback": True}
    hits.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    hits.sort(key=lambda row: _MATCH_RANK.get(str(row.get("gear_match") or ""), 9))
    return {"recipes": hits, "fallback": False}
