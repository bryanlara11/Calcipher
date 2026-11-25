"""Keep explicit dish/ingredient requests relevant before semantic ranking."""
import re
from app.services.brand_matcher import BRAND_ALIASES, normalize_brand

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
GENERIC.update("heavy heavier hearty big large larger substantial satisfying lighter small smaller lowest highest low lower higher best what whats which how many much does do has contains contain tell about it its this that would could should my today now naman ako ko gusto ba ang ng sa mga po please nutrition macros carbohydrate carbohydrates carbs fat fats price cost costs prices amount total s pls there available offer suggestions recommendations cal magkano".split())
GENERIC.update({"instead", "same", "but"})
SNACK_WORDS = {"sandwich", "sandwiches", "burger", "burgers", "yumburger", "hotdog", "hotdogs", "bun", "buns", "siopao", "buchi", "pie", "pies", "puff", "puffs", "pastry", "pastries", "brownie", "brownies", "fries", "nachos", "quesadilla", "quesadillas", "burrito", "burritos", "croissant", "dumpling", "dumplings", "siomai", "lemon squares", "cheese roll", "cheese rolls"}
SNACK_WORDS.update({"cheeseburger", "whopper", "tacos", "taco", "wrap", "wraps", "pizzawrap", "ensaymada", "bread", "nuggets", "wings", "chicken fingers", "slice"})


def search_order(query: str) -> str:
    words = set(normalize_brand(query).split())
    if words & {"heavy", "heavier", "hearty", "filling", "substantial", "big", "larger"}:
        return "calories_desc"
    if words & {"light", "lighter", "small", "smaller", "lowest", "lowestcalorie"}:
        return "calories_asc"
    return "semantic"


def question_field(query: str) -> str | None:
    """Only route explicit nutrition/price questions; meal recommendations stay searches."""
    query = normalize_brand(query)
    if re.search(r"\bhow much is\b|\bmagkano\b", query):
        return "price"
    followup = re.fullmatch(r"(?:and|what about|how about) (?:the )?(protein|carbs|fat|macros|calories|price)", query)
    if followup:
        return {"protein":"protein_g","carbs":"carbs_g","fat":"fat_g"}.get(followup[1], followup[1])
    units = r"calories|calorie|kcal|protein|carbs|carbohydrates|fat|macros|nutrition|price|cost"
    if not (re.search(rf"\bhow (?:many|much)\b.*\b(?:{units})\b", query)
            or re.search(rf"\b(?:{units})\s+(?:in|of|for|does)\b", query)
            or re.search(rf"\bwhat (?:is|are|s) (?:the )?(?:{units})\b", query)):
        return None
    if re.search(r"\b(price|cost)\b", query): return "price"
    if re.search(r"\b(macros|nutrition)\b", query): return "macros"
    if re.search(r"\bprotein\b", query): return "protein_g"
    if re.search(r"\b(carbs|carbohydrates)\b", query): return "carbs_g"
    if re.search(r"\bfat\b", query): return "fat_g"
    return "calories"

def food_pattern(query: str, brand: str | None = None) -> str | None:
    normalized = normalize_brand(query)
    if brand:
        names = [normalize_brand(brand), *[alias for alias, slug in BRAND_ALIASES.items() if slug == brand]]
        for name in sorted(names, key=len, reverse=True):
            normalized = re.sub(r"\b" + re.escape(name) + r"\b", " ", normalized)
    words = set(re.findall(r"[a-z]+", normalized)) - GENERIC
    snack = bool(re.search(r"\bsnacks?\b", normalized))
    if not words and not snack:
        return None
    groups = []
    for word in sorted(words):
        group = ALIASES.get(word, {word})
        if group not in groups:
            groups.append(group)
    if snack:
        groups.append(SNACK_WORDS)
    patterns = [r"\m(?:" + "|".join(re.escape(w) for w in sorted(g)) + r")\M" for g in groups]
    positive = "|".join(patterns) if " or " in f" {normalized} " else "".join(f"(?=.*{p})" for p in patterns) + ".*"
    # Snack requests should not be filled with explicitly named rice meals/drink bundles.
    return r"^(?!.*\m(?:rice|drinks?|burger steak)\M)(?:" + positive + ")" if snack else positive
