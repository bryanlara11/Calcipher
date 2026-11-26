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
# Words that describe the request rather than a dish ("highest protein item", "low carb options").
GENERIC.update("item items menu thing things ones top rank ranked ranking most least fewest greatest richest "
               "biggest smallest maximum minimum max min per serving servings carb carbohydrate protein "
               "calorie lowest highest content rich packed dense".split())
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

def _without_brand(query: str, brand: str | None) -> str:
    normalized = normalize_brand(query)
    if brand:
        names = [normalize_brand(brand), *[alias for alias, slug in BRAND_ALIASES.items() if slug == brand]]
        for name in sorted(names, key=len, reverse=True):
            normalized = re.sub(r"\b" + re.escape(name) + r"\b", " ", normalized)
        # Also drop spaced spellings of the brand ("mc donalds" for "mcdonalds").
        compact = {name.replace(" ", "") for name in names}
        tokens = normalized.split()
        for size in (3, 2):
            i = 0
            while i <= len(tokens) - size:
                if "".join(tokens[i:i + size]) in compact:
                    del tokens[i:i + size]
                else:
                    i += 1
        normalized = " ".join(tokens)
    return normalized


# Cooking styles, meal times and sizes. Names often leave them out ("Chicken Inasal" is grilled),
# so a search first requires them and, if nothing matches, drops them (see descriptor_terms).
DESCRIPTORS = {"fried", "grilled", "steamed", "spicy", "sweet", "savory", "vegetarian",
               "vegan", "large", "small", "breakfast", "lunch", "dinner"}


def _tokens(query: str, brand: str | None) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\d+\s*(?:pieces?|pcs?)\b|[a-z]+|\d+", _without_brand(query, brand))))


def catalog_patterns(query: str, brand: str | None, *, descriptors: bool = True) -> tuple[list[str], bool]:
    """Build bound PostgreSQL patterns for literal menu searches, retaining portion counts.

    With `descriptors=False`, words like "grilled" or "breakfast" are left out.
    """
    tokens = _tokens(query, brand)
    patterns = []
    for token in tokens:
        quantity = re.fullmatch(r"(\d+)\s*(?:pieces?|pcs?)", token)
        if quantity:
            patterns.append(r"\m" + quantity[1] + r"[[:space:].‐‑–—-]*(?:pcs?|pieces?)\M")
        elif token.isdigit():
            patterns.append(r"\m" + token + r"\M")
        elif token in {"snack", "snacks"}:
            patterns.append(_alternatives(SNACK_WORDS))
        elif token in DESCRIPTORS and token in GENERIC:
            if descriptors:
                patterns.append(_alternatives({token}))
        elif token not in GENERIC:
            patterns.append(_alternatives(ALIASES.get(token, {token})))
    return patterns, "or" in tokens


def descriptor_terms(query: str, brand: str | None) -> list[str]:
    """Descriptor words in a search that can be dropped when nothing matches them."""
    return [t for t in _tokens(query, brand) if t in DESCRIPTORS and t in GENERIC]


def search_terms(query: str, brand: str | None, *, descriptors: bool = True) -> str:
    """The food words a search matched on, for answers ("chicken 2pc"), in the user's order."""
    kept = DESCRIPTORS | {"snack", "snacks"} if descriptors else {"snack", "snacks"}
    return " ".join(t for t in _tokens(query, brand) if t[0].isdigit() or t not in GENERIC or t in kept)


def _alternatives(words) -> str:
    # Words of four letters or more match inside names ("chicken" finds "McChicken");
    # shorter ones match whole words so "pie" skips "piece" and "tea" skips "steak".
    return "(?:" + "|".join(re.escape(w) if len(w) > 3 else r"\m" + re.escape(w) + r"(?:e?s)?\M"
                            for w in sorted(words)) + ")"


def catalog_exclusion(query: str) -> str | None:
    """Snack requests leave out meals, rice and drink bundles, named in the item or its notes."""
    return r"\m(?:rice|drinks?|burger steak|meals?)\M" if re.search(r"\bsnacks?\b", normalize_brand(query)) else None


def food_pattern(query: str, brand: str | None = None) -> str | None:
    normalized = _without_brand(query, brand)
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
