"""Short, source-grounded replies for menu questions and snack preferences."""
import re
from app.services.brand_matcher import normalize_brand
from app.services.food_matching import search_order


def help_answer(message):
    if normalize_brand(message) in {"hi", "hello", "hey", "help", "what can you do", "how does this work", "how do i use this"}:
        return "Tell me what you feel like eating. Try ‘heavy snack’, ‘chicken under 500 kcal’, or ‘how many calories are in Lemon Squares?’ I can search menus and look up source nutrition; unknown values stay unknown."
    return None


def direct_answer(items, field):
    if not items:
        return "I don’t have a matching item in the current menu catalog. Try the restaurant name and dish together, or browse all brands in Search."
    lines = []
    for item in items:
        name = f"{item.brand_name} · {item.food_item}"
        if field == "calories":
            value = f"{item.calorie_label} kcal"
        elif field == "price":
            value = (f"Source price PHP {item.price_php:.2f}; confirm at the branch" if item.price_php is not None and item.is_verified and item.price_source_url else "No checked price is available")
        elif field == "macros":
            value = ", ".join(f"{label} {getattr(item,key):g} g" if getattr(item,key) is not None else f"{label} not listed" for label,key in [("Protein","protein_g"),("carbs","carbs_g"),("fat","fat_g")])
        else:
            label = {"protein_g":"Protein","carbs_g":"Carbs","fat_g":"Fat"}[field]
            value = f"{label}: {getattr(item,field):g} g" if getattr(item,field) is not None else f"{label} is not listed in the source"
        lines.append(f"{name}: {value}.")
    if len(items) > 1:
        lines.insert(0,"I found several menu entries. Check the item and serving below:")
    if field != "price" and any(not item.serving_size for item in items):
        lines.append("Some serving sizes are not listed; I haven’t converted them to a different portion.")
    if any(not item.is_verified for item in items):
        lines.append("These source values are pending review.")
    return "\n".join(lines)


NUTRIENT_LABELS = {"calories": "calories", "protein_g": "protein", "carbs_g": "carbs", "fat_g": "fat"}
_MACROS = (("protein_g", "protein"), ("carbs_g", "carbs"), ("fat_g", "fat"))
RANKED_LINES = 5


def _plural(count, word, plural=None):
    return f"{count} {word if count == 1 else plural or word + 's'}"


def _join(parts):
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def describe_limits(c):
    """The active numeric limits, in words, so an answer says exactly what was applied."""
    out = []
    if c.min_calories is not None:
        out.append(f"at least {c.min_calories} kcal")
    if c.max_calories is not None:
        out.append(f"at most {c.max_calories} kcal")
    for key, label in _MACROS:
        low, high = getattr(c, f"min_{key}"), getattr(c, f"max_{key}")
        if low is not None:
            out.append(f"at least {low:g} g {label}")
        if high is not None:
            out.append(f"at most {high:g} g {label}")
    if c.max_price_php is not None:
        out.append(f"a checked price of PHP {c.max_price_php:.2f} or less")
    return out


def _rank_value(item, field):
    return item.calories if field == "calories" else getattr(item, field)


def _item_facts(item, lead=None):
    """'45 g protein · 520 kcal · 20 g fat (carbs not listed)'. Missing values are named, never guessed."""
    parts, missing = [], []
    if lead and lead != "calories":
        parts.append(f"{getattr(item, lead):g} g {NUTRIENT_LABELS[lead]}")
    parts.append(f"{item.calorie_label} kcal")
    for key, label in _MACROS:
        if key == lead:
            continue
        value = getattr(item, key)
        (missing.append(label) if value is None else parts.append(f"{value:g} g {label}"))
    text = " · ".join(parts)
    return text + (f" ({_join(missing)} not listed)" if missing else "")


def unstored_answer(nutrients):
    names = _join(nutrients)
    return (f"The CalCipher menu catalog doesn’t record {names}, so I can’t rank or filter by "
            f"{'it' if len(nutrients) == 1 else 'them'}. It lists calories, protein, carbs and fat. "
            "Try asking for one of those instead.")


def menu_answer(items, total, constraints, ranking=None, *, missing_count=0, brand_name=None, terms="", unknown_place=None):
    """Answer for a catalog-wide search, built only from the returned rows and the applied limits."""
    limits = describe_limits(constraints)
    what = f" matching “{terms}”" if terms else ""
    where = f" at {brand_name}" if brand_name else ""
    within = f" with {_join(limits)}" if limits else ""
    if not items:
        if ranking and missing_count:
            label = NUTRIENT_LABELS[ranking.field]
            return (f"{_plural(missing_count, 'menu item')}{what}{where}{within} "
                    f"{'matches' if missing_count == 1 else 'match'}, but none list {label}, "
                    f"so I can’t rank them by {label}.")
        if unknown_place and not brand_name:
            return (f"I couldn’t find a restaurant or menu item called “{unknown_place}” in the CalCipher catalog. "
                    "Browse all brands in Search to see which restaurants are covered.")
        message = f"No menu items{what}{where}{within} are in the current catalog."
        if limits:
            message += f" Try loosening or removing a limit ({'; '.join(limits)})."
        elif terms:
            message += " Try a shorter or different food word, or browse all brands in Search."
        if constraints.max_price_php is not None:
            message += " Items without a verified price are excluded from budget searches."
        return message

    if ranking:
        field = ranking.field
        label = NUTRIENT_LABELS[field]
        best = "Highest" if ranking.direction == "desc" else "Lowest"
        lines = [f"{best} {label}{where}{what}{within}:"]
        for n, item in enumerate(items[:RANKED_LINES], 1):
            lines.append(f"{n}. {item.food_item} — {item.brand_name}: {_item_facts(item, field)}"
                         + (f" · {item.serving_size}" if item.serving_size else "")
                         + (f" · {item.menu_note}" if item.menu_note else ""))
        top = _rank_value(items[0], field)
        ties = sum(1 for item in items if _rank_value(item, field) == top)
        if ties > 1:
            shown = f"{top:g} kcal" if field == "calories" else f"{top:g} g"
            lines.append(f"{'At least ' if ties == len(items) and total > len(items) else ''}{ties} items tie at {shown}.")
        if total > RANKED_LINES:
            lines.append(f"Top {min(len(items), RANKED_LINES)} of {total} ranked items shown here; "
                         f"the cards list the top {len(items)}.")
        if missing_count:
            lines.append(f"{_plural(missing_count, 'matching item')} {'doesn’t' if missing_count == 1 else 'don’t'} "
                         f"list {label}, so {'it isn’t' if missing_count == 1 else 'they aren’t'} ranked.")
        if field == "calories" and any(item.calories_min is not None for item in items):
            lines.append("Calorie ranges are ranked by their upper value.")
    else:
        restaurants = len({item.brand_name for item in items})
        lines = [f"{_plural(total, 'menu item')}{what}{where}{within} from "
                 f"{_plural(restaurants, 'restaurant')}. Expand a restaurant to see its meals."]
        if total > len(items):
            lines = [f"{_plural(total, 'menu item')}{what}{where}{within}. Showing the first {len(items)} "
                     f"from {_plural(restaurants, 'restaurant')}; open Search to see all of them."]
    if any(not item.is_verified for item in items):
        lines.append("Source values are pending review.")
    return "\n".join(lines)


def snack_answer(items, query, max_calories=None):
    if not items or not re.search(r"\bsnacks?\b", normalize_brand(query)):
        return None
    order = search_order(query)
    if order == "semantic":
        return None
    if order == "calories_desc":
        lead = ("I looked for substantial snack portions" if max_calories is None or max_calories >= 300
                else "Snack matches within your calorie limit, starting with higher-calorie portions")
    else:
        lead = "Lower-calorie snack matches first"
    first = items[0]
    return (f"{lead}. {first.food_item} at {first.brand_name} is listed at {first.calorie_label} kcal. "
            "Compare the portions and other options below."
            + (" Source values are pending review." if any(not item.is_verified for item in items) else ""))
