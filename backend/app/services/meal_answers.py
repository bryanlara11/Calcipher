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
