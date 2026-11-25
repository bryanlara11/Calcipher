"""Keep explicit dish/ingredient requests relevant before semantic ranking."""
import re
from app.services.brand_matcher import normalize_brand

ALIASES = {}
for group in [{"banana","bananas","saging","plantain","plantains"}, {"chicken","manok"}, {"beef","baka"}, {"pork","baboy"}, {"salad","salads"}]:
    for word in group: ALIASES[word] = group
for word, values in {
    "fish": {"fish","isda","salmon","tuna","bangus","tilapia"},
    "noodle": {"noodle","noodles","udon","ramen","pancit","canton","bihon"},
    "pasta": {"pasta","spaghetti","carbonara","penne","linguine"},
    "coffee": {"coffee","latte","mocha","americano","cappuccino","cold brew"},
    "bread": {"bread","breads","bun","buns","roll","rolls"},
}.items(): ALIASES[word] = values
ALIASES["noodles"] = ALIASES["noodle"]
ALIASES["isda"] = ALIASES["fish"]
GENERIC = set("find show give get recommend suggest search looking look want craving please me some something anything options option food meal meals restaurant restaurants dish dishes nearby near filling light healthy tasty delicious cheap affordable high protein grilled fried steamed spicy sweet savory savoury vegetarian vegan lunch dinner breakfast snack snacks style under below above least at most calories calorie kcal grams gram g php pesos peso km within distance more less than and with for from in a an the is are of to i can you like eat have eating or without no".split())

def food_pattern(query: str, brand: str | None = None) -> str | None:
    normalized = normalize_brand(query)
    if brand:
        normalized = re.sub(r"\b" + re.escape(normalize_brand(brand)) + r"\b", " ", normalized)
    words = set(re.findall(r"[a-z]+", normalized)) - GENERIC
    if not words:
        return None
    groups = []
    for word in sorted(words):
        group = ALIASES.get(word, {word})
        if group not in groups:
            groups.append(group)
    patterns = [r"\m(?:" + "|".join(re.escape(w) for w in sorted(g)) + r")\M" for g in groups]
    return "|".join(patterns) if " or " in f" {normalized} " else "".join(f"(?=.*{p})" for p in patterns) + ".*"
